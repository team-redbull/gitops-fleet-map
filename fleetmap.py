#!/usr/bin/env python3
"""Fleet Map: cluster x tenant matrix from multi-tenant day2 GitOps repos."""
import argparse, collections, datetime, glob, http.server, json, math, os, posixpath, re, shutil, ssl
import sys, tarfile, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPE = {"mce": "mces", "hosted": "hosted-clusters", "upi": "upi"}
SCOPE_LABEL = {"mce": "MCEs", "hosted": "hosted clusters", "upi": "UPI clusters"}
SECRET = re.compile(r"pass|secret|token|key|cert|cred|auth", re.I)
HIDDEN = "\u2022\u2022\u2022 (hidden)"
OPT = {"hide": False}  # --hide-values / HIDE_VALUES=true: keys and origins only, no values in the page
MODEL_LOCK = threading.Lock()  # WARNINGS and OPT are globals: one model build at a time, and no tree swap while one scans
VIEW_LOCK = threading.Lock()  # one on-demand view (downloads + render) at a time; identical requests queue and then hit VIEWS
VIEWS = collections.OrderedDict()  # (sorted (tenant, sha), day1 sha) -> model; LRU
MAX_VIEWS = 20
NO_DAY1 = ("No day1 source configured: the cluster universe is the union of all tenant folders, "
           "so stale clusters cannot be detected. 'available' means another tenant has the cluster.")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------- fetching: GitLab API only (branch SHA check, archive download on change) ----------
STAGGER = 1.0  # seconds between archive downloads within one cycle (GitLab rate-limits archives)
SCOPES = ("read_api", "read_repository")


class Redactor:
    secrets = []

    @classmethod
    def clean(cls, s):
        for x in cls.secrets:
            s = s.replace(x, "***")
        return s


class ConfigError(Exception):
    pass


class Wait(Exception):  # some project has no tree yet (rate limited, rejected archive): keep the last output
    pass


class ViewError(Exception):  # a /api/view request that cannot be served: code is the HTTP status
    def __init__(self, msg, code=400, retry=None):
        super().__init__(msg)
        self.code, self.retry = code, retry


class GitlabError(Exception):
    def __init__(self, msg, code=0, retry=None, body=b""):
        super().__init__(msg)
        self.code, self.retry, self.body = code, retry, body


class NoRedirect(urllib.request.HTTPRedirectHandler):  # a redirect would carry the token elsewhere; surface it instead
    def redirect_request(self, *a, **k):
        return None


def split_url(u):
    """Web URL -> ('https://host', [path segments]); a trailing slash, '.git' and '/-/tree/main'-style suffixes are dropped."""
    s = urllib.parse.urlsplit(u.strip())
    if s.scheme not in ("http", "https") or not s.netloc:
        raise ConfigError(f"not a web URL: {u!r} (expected e.g. https://gitlab.example/group/subgroup)")
    segs = [urllib.parse.unquote(x) for x in s.path.split("/") if x]
    segs = segs[:segs.index("-")] if "-" in segs else segs
    if segs and segs[-1].endswith(".git"):
        segs[-1] = segs[-1][:-4]
    return f"{s.scheme}://{s.netloc}", segs


def explain(e, what):
    """GitlabError -> one actionable line."""
    c = e.code
    if c == 401:
        return "GitLab rejected the token (401): check GITLAB_TOKEN / GITLAB_TOKEN_FILE; it may be wrong, revoked or expired"
    if c == 403:
        return f"token may not read {what} (403): it needs the scopes read_api + read_repository"
    if c == 404:
        return f"{what} not found or token can't see it: check the URL and that the token's user has at least Reporter on it"
    if c in (301, 302, 307, 308):
        return f"{what}: GitLab redirects to {e.retry}; use that address (usually http -> https) in the URL"
    return f"{what}: {e}"


class Fetcher:
    """Keeps CACHE_DIR/{sigs/<tenant>,day1} equal to the branch heads of the sigs group's projects (and day1), downloading only what changed."""

    def __init__(self, env):
        g = lambda k: (env.get(k) or "").strip()
        if g("GITLAB_TOKEN_FILE"):  # a mounted Secret wins over the env var
            try:
                self.token = open(g("GITLAB_TOKEN_FILE")).read().strip()
            except OSError as e:
                raise ConfigError(f"GITLAB_TOKEN_FILE {g('GITLAB_TOKEN_FILE')}: {e.strerror}")
        else:
            self.token = g("GITLAB_TOKEN")
        if not self.token:
            raise ConfigError("set GITLAB_TOKEN or GITLAB_TOKEN_FILE (scopes read_api + read_repository)")
        Redactor.secrets.append(self.token)
        self.base = self.group = self.day1_path = self.day1_url = None
        self.prefix = []
        if g("SIGS_URL"):
            self.host, self.segs = split_url(g("SIGS_URL"))
            if not self.segs:
                raise ConfigError("SIGS_URL has no group path: expected e.g. https://gitlab.example/org/day2/sigs")
            self.day1_url = split_url(g("DAY1_URL")) if g("DAY1_URL") else None
        elif g("GITLAB_URL") and g("SIGS_GROUP"):  # old settings
            self.base, self.group, self.day1_path = g("GITLAB_URL").rstrip("/"), g("SIGS_GROUP").strip("/"), g("DAY1_PROJECT").strip("/") or None
        else:
            raise ConfigError("set SIGS_URL (web URL of the sigs group, e.g. https://gitlab.example/org/day2/sigs) and GITLAB_TOKEN")
        self.ref, self.cache = g("GIT_REF") or None, g("CACHE_DIR") or "/tmp/fleet-cache"
        self.interval = int(g("INTERVAL") or 60)
        try:
            self.max_trees = int(g("MAX_CACHED_TREES") or 30)  # non-default branch trees kept on disk (LRU)
        except ValueError:
            raise ConfigError("MAX_CACHED_TREES must be a number")
        self.prefetch = [x.strip() for x in g("PREFETCH_REFS").split(",") if x.strip()]  # e.g. a:dev,b:release, rendered each cycle
        if g("GITLAB_INSECURE").lower() == "true":
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        else:
            ctx = ssl.create_default_context()  # system CAs ...
            if g("GITLAB_CA_FILE"):
                try:
                    ctx.load_verify_locations(g("GITLAB_CA_FILE"))  # ... plus this one
                except (OSError, ssl.SSLError) as e:
                    raise ConfigError(f"GITLAB_CA_FILE {g('GITLAB_CA_FILE')}: {e}")
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
        try:
            os.makedirs(self.cache, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"CACHE_DIR {self.cache}: {e.strerror}")
        try:
            self.state = json.load(open(f"{self.cache}/state.json"))  # {"sigs/<name>"|"day1": {id, sha}}
        except (OSError, ValueError):
            self.state = {}
        for d in glob.glob(f"{self.cache}/tmp*"):  # leftovers of a killed download
            shutil.rmtree(d, ignore_errors=True)
        self.views_dl = 0  # archives downloaded for views
        self.brs = {}  # tenant -> dict(id, default, br={branch: sha}) from the last branch list
        self.version = self.day1 = None
        self.pause, self.last, self.bad, self.calls, self.checked = 0, None, {}, 0, False  # last: shas of the output on disk

    # --- HTTP
    def req(self, url, timeout=60):
        self.calls += 1
        try:
            return self.opener.open(urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token, "User-Agent": "gitops-fleet-map"}), timeout=timeout)
        except urllib.error.HTTPError as e:
            raise GitlabError(f"HTTP {e.code}", e.code, e.headers.get("Location") if e.code in (301, 302, 307, 308) else e.headers.get("Retry-After"), e.read(300))
        except urllib.error.URLError as e:
            host = urllib.parse.urlsplit(url).netloc
            if isinstance(e.reason, ssl.SSLError):
                raise GitlabError(f"TLS check of {host} failed ({e.reason.reason or e.reason}): set GITLAB_CA_FILE to your CA's PEM file, or GITLAB_INSECURE=true")
            raise GitlabError(f"cannot reach {host}: {e.reason}")
        except OSError as e:
            raise GitlabError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {e}")

    def get(self, path):
        with self.req(f"{self.base}/api/v4/{path}") as r:
            try:
                return json.load(r), r.headers
            except ValueError:
                raise GitlabError(f"{self.base} did not answer with GitLab API JSON")

    # --- startup
    def probe(self, base):
        """GitLab's version info if base is a GitLab root; None if not; error for a wrong token (only a real GitLab answers 401 with JSON)."""
        try:
            with self.req(f"{base}/api/v4/version") as r:
                v = json.load(r)
            return v if isinstance(v, dict) and "version" in v else None
        except GitlabError as e:
            if e.code == 401 and e.body.lstrip().startswith(b"{"):
                raise GitlabError(explain(e, ""), 401)
            if e.code:
                return None
            raise
        except ValueError:
            return None

    def setup(self):
        if self.version:
            return
        try:
            if self.base:
                self.version = self.get("version")[0].get("version")
            else:  # GitLab may live under a sub-path: longest prefix of the URL path first; the group needs >= 1 segment
                tried = []
                for k in range(len(self.segs) - 1, -1, -1):
                    base = self.host + "".join("/" + s for s in self.segs[:k])
                    v = self.probe(base)
                    if v:
                        self.base, self.prefix, self.version = base, self.segs[:k], v["version"]
                        self.group = "/".join(self.segs[k:])
                        break
                    tried.append(base)
                if not self.base:
                    raise GitlabError(f"no GitLab API found at {', '.join(tried)} (/api/v4/version): check SIGS_URL is the web URL of the sigs group")
        except GitlabError as e:
            raise GitlabError(explain(e, "GitLab") if e.code else str(e), e.code)
        if self.day1_url:
            host, segs = self.day1_url
            if host != self.host or segs[:len(self.prefix)] != self.prefix or len(segs) == len(self.prefix):
                raise ConfigError(f"DAY1_URL must be a project on the same GitLab as SIGS_URL ({self.base}/...)")
            self.day1_path = "/".join(segs[len(self.prefix):])
        self.check_token()
        if self.day1_path:
            try:
                p = self.get("projects/" + urllib.parse.quote(self.day1_path, safe=""))[0]
            except GitlabError as e:
                raise GitlabError(explain(e, "DAY1_URL project"), e.code)
            if not p.get("default_branch") and not self.ref:
                raise GitlabError("DAY1_URL project has no commits yet")
            self.day1 = dict(id=p["id"], ref=self.ref or p["default_branch"])

    def check_token(self):
        try:
            d = self.get("personal_access_tokens/self")[0]
        except GitlabError as e:
            log(f"warn: could not read token info (GET personal_access_tokens/self: {e}); continuing")
            return
        sc, exp = d.get("scopes") or [], d.get("expires_at")
        log(f"token: scopes {', '.join(sc) or '(none)'}; expires {exp or 'never'}")
        miss = [s for s in SCOPES if s not in sc and "api" not in sc]
        if miss:
            log(f"warn: token is missing scope(s) {', '.join(miss)}: reads will fail with 403; create the token with read_api + read_repository")
        try:
            days = (datetime.date.fromisoformat(exp) - datetime.date.today()).days if exp else None
        except ValueError:
            days = None
        if days is not None and days <= 14:
            log(f"warn: token {'EXPIRED' if days < 0 else 'expires in %d days' % days} ({exp}): create a new one and update the Secret")

    # --- one cycle
    def projects(self):
        url, out, page = f"groups/{urllib.parse.quote(self.group, safe='')}/projects?include_subgroups=true&archived=false&per_page=100", [], "1"
        while page:
            try:
                d, h = self.get(f"{url}&page={page}")
            except GitlabError as e:
                raise GitlabError(explain(e, "SIGS_URL group") if e.code else str(e), e.code)
            out += d
            page = h.get("X-Next-Page")
        return out

    def save(self):
        atomic(f"{self.cache}/state.json", json.dumps(self.state))

    def sync(self):
        """-> (sigs_dir, day1_dir|None, {name: sha}) with every project's tree in the cache; Wait if one is missing."""
        self.calls, t0 = 0, time.time()
        self.setup()
        items, dl = {}, 0
        for p in self.projects():
            if f"sigs/{p['path']}" in items:
                log(f"warn: two projects named {p['path']} in the group; using the first")
            elif p.get("default_branch") or self.ref:
                items[f"sigs/{p['path']}"] = (p["id"], self.ref or p["default_branch"])
            else:
                log(f"skip {p['path_with_namespace']}: empty repository")
        if self.day1:
            items["day1"] = (self.day1["id"], self.day1["ref"])
        if not self.checked:
            self.checked = True
            log(f"config: GitLab {self.base} ({self.version}); sigs group {self.group}: {sum(k != 'day1' for k in items)} tenant projects; "
                f"day1 {'on: ' + self.day1_path if self.day1 else 'off'}; interval {self.interval}s; ref {self.ref or 'default branch'}; cache {self.cache}")
        for t in [t for t in self.brs if f"sigs/{t}" not in items]:
            del self.brs[t]
        for k in [k for k in self.state if k not in items]:  # left the group
            shutil.rmtree(f"{self.cache}/{k}", ignore_errors=True)
            shutil.rmtree(f"{self.cache}/trees/{self.state[k]['id']}", ignore_errors=True)
            del self.state[k]
            self.save()
            log(f"{k}: no longer in GitLab, dropped")
        for k, (pid, ref) in items.items():
            try:
                if k == "day1":  # day1 stays on its default branch: one call. To offer day1 refs later, list its branches like the tenants.
                    sha = self.get(f"projects/{pid}/repository/branches/{urllib.parse.quote(ref, safe='')}")[0]["commit"]["id"]
                else:  # every branch + sha in one call per project; nothing is downloaded for the non-default ones
                    bl = self.branch_list(pid)
                    if ref not in bl:
                        raise GitlabError("no such branch")
                    sha, self.brs[k[5:]] = bl[ref], dict(id=pid, default=ref, br=bl)
            except (GitlabError, KeyError, TypeError) as e:
                log(f"warn: {k}: cannot read branch {ref} ({e}); keeping what is cached")
                continue
            have = self.state.get(k)
            if (have and have["id"] == pid and have["sha"] == sha and os.path.isdir(f"{self.cache}/{k}")) or self.bad.get(k) == sha:
                continue
            if time.time() < self.pause:
                continue
            if dl:
                time.sleep(STAGGER)
            dl += 1
            log(f"{k}: {sha[:7]}, downloading archive")
            try:
                self.download(f"{self.cache}/{k}", pid, sha)
            except GitlabError as e:
                if e.code == 429:  # ponytail: one shared pause; GitLab's limit is per user, so the rest would 429 too
                    wait = min(int(e.retry) if str(e.retry).isdigit() else 60, 3600)
                    self.pause = time.time() + wait
                    log(f"rate limited on {k}: pausing downloads {wait}s, keeping old data, retrying next cycle")
                else:
                    log(f"warn: {k}: archive download failed ({explain(e, 'archive')}); keeping old data")
                continue
            except (tarfile.TarError, ValueError, OSError) as e:
                self.bad[k] = sha  # not retried until the project gets another commit
                log(f"warn: {k}: archive rejected ({e}); keeping old data")
                continue
            self.state[k] = dict(id=pid, sha=sha)
            self.save()
        miss = [k for k in items if k not in self.state or not os.path.isdir(f"{self.cache}/{k}")]
        log(f"fetch: {len(items)} projects, {dl} archive downloads, {self.calls} API calls, {time.time() - t0:.1f}s")
        if miss:
            raise Wait(f"{len(items) - len(miss)}/{len(items)} projects fetched; waiting for {', '.join(miss)}; keeping the last output")
        return f"{self.cache}/sigs", f"{self.cache}/day1" if self.day1 else None, {(k[5:] if k != "day1" else "(day1)"): self.state[k]["sha"] for k in items}

    def branch_list(self, pid):
        """{branch: sha} of a project: GET repository/branches (each entry has commit.id), 100 per page, following X-Next-Page."""
        out, page = {}, "1"
        while page:
            d, h = self.get(f"projects/{pid}/repository/branches?per_page=100&page={page}")
            out.update({b["name"]: b["commit"]["id"] for b in d})
            page = h.get("X-Next-Page")
        return out

    def download(self, dest, pid, sha):
        tmp = tempfile.mkdtemp(prefix="tmp-", dir=self.cache)
        try:
            with self.req(f"{self.base}/api/v4/projects/{pid}/repository/archive.tar.gz?sha={sha}", 300) as r, open(f"{tmp}/a.tgz", "wb") as f:
                shutil.copyfileobj(r, f)
            tree = extract(f"{tmp}/a.tgz", f"{tmp}/x")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with MODEL_LOCK:  # a view render may be scanning dest; swap: the old tree moves into tmp, which is removed below
                if os.path.isdir(dest):
                    os.rename(dest, f"{tmp}/old")
                os.rename(tree, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # --- on-demand branch views
    def names(self):
        return {t: sorted(e["br"]) for t, e in sorted(self.brs.items())}

    def default_refs(self):
        return {t: dict(branch=e["default"], sha=self.state[f"sigs/{t}"]["sha"], default=True) for t, e in self.brs.items() if f"sigs/{t}" in self.state}

    def pick(self, refs):
        """['tenant:branch', ...] -> {tenant: dict(branch, sha, default, id)}. Only exact tenant and branch names from the stored lists pass;
        nothing else from the request ever reaches a URL or a path."""
        if not self.brs:
            raise ViewError("No data yet: the first fetch from GitLab has not succeeded. Try again shortly.", 503, 5)
        out = {}
        for r in refs:
            t, sep, b = r.partition(":")
            e = self.brs.get(t) if sep else None
            if e is None:
                raise ViewError(f"unknown tenant {t[:60]!r} (expected tenant:branch)")
            if b not in e["br"]:
                raise ViewError(f"tenant {t} has no branch {b[:80]!r}")
            if t in out:
                raise ViewError(f"tenant {t} given twice")
            out[t] = dict(branch=b, sha=e["br"][b], default=b == e["default"], id=e["id"])
        return out

    def tree_for(self, t, w):
        """Directory of tenant t's non-default branch tree (cached by sha); downloads it if missing."""
        d = f"{self.cache}/trees/{w['id']}/{w['sha']}"
        if not os.path.isdir(d):
            left = math.ceil(self.pause - time.time())
            if left > 0:
                raise ViewError(f"GitLab is rate limiting archive downloads; try again in {left}s", 503, left)
            log(f"view {t}@{w['branch']}: {w['sha'][:7]} downloading archive")
            self.views_dl += 1
            try:
                self.download(d, w["id"], w["sha"])
            except GitlabError as e:
                if e.code == 429:
                    wait = min(int(e.retry) if str(e.retry).isdigit() else 60, 3600)
                    self.pause = time.time() + wait
                    log(f"rate limited on view {t}@{w['branch']}: pausing downloads {wait}s")
                    raise ViewError(f"GitLab is rate limiting archive downloads; try again in {wait}s", 503, wait)
                raise ViewError(explain(e, "archive"), 502)
            except (tarfile.TarError, ValueError, OSError) as e:
                raise ViewError(f"archive of {t}@{w['branch']} rejected ({e})", 502)
        os.utime(d)  # LRU stamp
        return d

    def evict(self, keep):
        """Keep at most max_trees non-default trees, dropping the least recently used. Default-branch trees live in sigs/ and are never touched."""
        ts = sorted(glob.glob(f"{self.cache}/trees/*/*"), key=os.path.getmtime)
        for d in ts[:max(0, len(ts) - self.max_trees)]:
            if d not in keep:
                shutil.rmtree(d, ignore_errors=True)


def extract(tgz, out):
    """Unpack a GitLab archive into out and return the tree inside its single top-level dir. Rejects absolute paths, '..',
    links leading out of the tree and device files before anything is written; tarfile's 'data' filter backs that up."""
    os.makedirs(out)
    with tarfile.open(tgz) as tf:
        tops = set()
        for m in tf.getmembers():
            parts = posixpath.normpath(m.name).split("/")
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise ValueError(f"unsafe path {m.name!r}")
            if m.isdev():
                raise ValueError(f"device file {m.name!r}")
            tops.add(parts[0])
            if m.issym() or m.islnk():
                t = posixpath.normpath(posixpath.join(posixpath.dirname(m.name), m.linkname) if m.issym() else m.linkname)
                if m.linkname.startswith("/") or t.split("/")[0] != parts[0]:
                    raise ValueError(f"link {m.name!r} leaves the tree")
        if len(tops) > 1:
            raise ValueError("archive has no single top-level directory")
        tf.extractall(out, filter="data")
    tree = os.path.join(out, tops.pop()) if tops else out
    if not os.path.isdir(tree):
        raise ValueError("archive top level is not a directory")
    return tree


# ---------- scanning ----------
def dirs(p):
    return sorted(d for d in os.listdir(p) if not d.startswith(".") and os.path.isdir(os.path.join(p, d))) if os.path.isdir(p) else []


WARNINGS = []  # unreadable/malformed files of the current run; shown in the page


def warn(msg):
    log("warn:", msg)
    if msg not in WARNINGS:
        WARNINGS.append(msg)


def yload(p, label=None):
    """Always a dict: missing, unparseable or non-mapping files count as empty (and are reported)."""
    label = label or p
    try:
        with open(p) as f:
            d = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        warn(f"{label}: not valid YAML, ignored")
        return {}
    if d is None:
        return {}
    if not isinstance(d, dict):
        warn(f"{label}: expected key/value content, got {type(d).__name__}; ignored")
        return {}
    return d


def tag(t):
    return str(t).split("-")[0] if t else None


def ocp(path, label=None):
    return tag(yload(path, label).get("mastertag"))


def fpath(kind, site, env, mce, name, create=False):
    """Repo folder of a cluster in a tenant repo (create=True: where to add it)."""
    if kind == "hub":
        return "defaults/hub/"
    b = f"sites/{site}/{env or '<env>'}/"  # day1 names that don't follow ocp4-<prod|prep|test>-... have no known env
    if kind == "upi":
        return f"{b}upi/{name}/"
    if kind == "mce":
        return f"{b}mces/{name}/" + ("in-cluster/" if create else "")
    return f"{b}mces/{mce}/{name}/"


def scan_tenant(root, tn):
    """-> {cluster: {kind, site, env, mce, version, path, dup?, charts{name: (own|default, scope)}}}"""
    dflt = {s: dirs(f"{root}/defaults/{s}") for s in list(SCOPE.values()) + ["hub"]}

    def excl_for(scope):
        rel = f"defaults/{scope}/exclusions.yaml"
        v = yload(f"{root}/{rel}", f"{tn}/{rel}").get("exclusions")
        if v is not None and not isinstance(v, dict):
            warn(f"{tn}/{rel}: 'exclusions' must map chart names to cluster lists; ignored")
            return {}
        v = v or {}
        for c, x in v.items():
            if not isinstance(x, list):
                warn(f"{tn}/{rel}: exclusions for '{c}' is not a list; ignored")
        return {c: x for c, x in v.items() if isinstance(x, list)}

    excl = {s: excl_for(s) for s in SCOPE.values()}
    cl = {}

    def add(name, kind, site, env, mce, own, version=None):
        path = fpath(kind, site, env, mce, name)
        if name in cl:  # same cluster folder twice in one repo: keep the first, remember all
            cl[name].setdefault("dup", [cl[name]["path"]]).append(path)
            return
        charts = {c: ("own", "") for c in own}
        ex = [c for c in dflt[SCOPE[kind]] if name in excl[SCOPE[kind]].get(c, [])]
        for c in dflt[SCOPE[kind]]:
            if c not in ex:
                charts.setdefault(c, ("default", SCOPE[kind]))
        cl[name] = dict(kind=kind, site=site, env=env, mce=mce, version=version, path=path, charts=dict(sorted(charts.items())),
                        excluded=[c for c in ex if c not in charts])

    for site in dirs(f"{root}/sites"):
        for env in dirs(f"{root}/sites/{site}"):
            e = f"{root}/sites/{site}/{env}"
            for mce in dirs(f"{e}/mces"):
                add(mce, "mce", site, env, mce, dirs(f"{e}/mces/{mce}/in-cluster"))
                for h in dirs(f"{e}/mces/{mce}"):
                    if h != "in-cluster":
                        add(h, "hosted", site, env, mce, dirs(f"{e}/mces/{mce}/{h}"))
            for u in dirs(f"{e}/upi"):
                add(u, "upi", site, env, "", dirs(f"{e}/upi/{u}"),
                    ocp(f"{e}/upi/{u}/version.yaml", f"{tn}/sites/{site}/{env}/upi/{u}/version.yaml"))
    if dflt["hub"]:
        cl["prod-hub"] = dict(kind="hub", site="", env="", mce="", version=None, path=fpath("hub", "", "", "", ""),
                              charts={c: ("default", "hub") for c in dflt["hub"]})
    return cl


ENVS = ("prod", "prep", "test")


def copy_to_sigs(*layers):
    """Effective day2.copy_to_sigs over day1's merge order (configValues, site, mce, cluster). Lists REPLACE; null = none."""
    out = []
    for d in layers:
        day2 = d.get("day2")
        if isinstance(day2, dict) and "copy_to_sigs" in day2:
            v = day2["copy_to_sigs"]
            out = [] if v is None else v if isinstance(v, list) else out
    return list(dict.fromkeys(str(x) for x in out))


def scan_day1(root):
    out = {}
    # only prod|prep|test count as an env: 'ocp4-upgrade-demo' / 'ocp4-mce-lab' have none (env check is then skipped)
    env = lambda n: n.split("-")[1] if n.count("-") > 1 and n.split("-")[1] in ENVS else ""
    cfg = yload(f"{root}/sites/configValues.yaml", "day1/sites/configValues.yaml")
    for site in dirs(f"{root}/sites"):
        sv = yload(f"{root}/sites/{site}/values.yaml", f"day1/sites/{site}/values.yaml")
        for mce in dirs(f"{root}/sites/{site}/mces"):
            m = f"{root}/sites/{site}/mces/{mce}"
            mv = yload(f"{m}/values.yaml", f"day1/sites/{site}/mces/{mce}/values.yaml")
            out[mce] = dict(kind="mce", site=site, env=env(mce), mce=mce,
                            version=ocp(f"{m}/version.yaml", f"day1/sites/{site}/mces/{mce}/version.yaml"))
            hc, seen = f"{m}/hostedClusters", set()
            for f in sorted(os.listdir(hc)) if os.path.isdir(hc) else []:
                n, ext = os.path.splitext(f)
                if ext not in (".yaml", ".yml") or n in seen:  # hcAppset takes both; day2 reads only .yaml
                    continue
                seen.add(n)
                d = yload(f"{hc}/{f}", f"day1/sites/{site}/mces/{mce}/hostedClusters/{f}")
                v = tag(d.get("mastertag"))
                np = d.get("nodepool") if isinstance(d.get("nodepool"), list) else []
                out[n] = dict(kind="hosted", site=site, env=env(n), mce=mce, version=v,
                              expected=copy_to_sigs(cfg, sv, mv, d),
                              workers=sorted({tag(p.get("workertag")) for p in np if isinstance(p, dict) and p.get("workertag")} - {v}),
                              warns=["day1 file is .yml; day2 reads only .yaml: render will fail (required mastertag)"] if ext == ".yml" else [])
    return out


# ---------- model ----------
def build(tenants, day1):
    rows = {}
    for name, d in (day1 or {}).items():
        rows[name] = dict(d, name=name, source="day1")
    for cl in tenants.values():
        for name, c in cl.items():
            r = rows.setdefault(name, dict(name=name, kind=c["kind"], site=c["site"], env=c["env"], mce=c["mce"],
                                            version=None, source="sigs-only"))
            r["version"] = r["version"] or c["version"]
    for r in rows.values():
        r["cells"] = {}
    summary = {t: dict(stale=[], available=[], missing=[]) for t in tenants}
    for t, cl in tenants.items():
        for name, r in rows.items():
            c, k = cl.get(name), r["kind"]
            cell = dict(status="none", charts=[], reason="", path="")
            if c:
                cell["charts"] = [dict(name=n, origin=o, scope=sc) for n, (o, sc) in c["charts"].items()]
                cell.update(status="onboarded", path=c["path"])
                if c.get("dup"):
                    cell.update(status="stale", reason="folder exists twice: " + ", ".join(c["dup"]))
                elif k == "upi":
                    cell["reason"] = "unverified (no day1 source)"
                elif day1 is not None and k in ("mce", "hosted"):
                    d = day1.get(name)
                    if not d:
                        cell.update(status="stale", reason="not in day1")
                    elif (d["site"], d["mce"]) != (c["site"], c["mce"]) or (d["env"] and d["env"] != c["env"]):
                        cell.update(status="stale", reason=f"day1 has it at site {d['site']} / env {d['env'] or '?'} / mce {d['mce']}, "
                                    f"tenant folder is at site {c['site']} / env {c['env']} / mce {c['mce']}")
                if cell["status"] == "onboarded" and r.get("expected") is not None and k == "hosted":
                    cell["reg"] = "day1" if t in r["expected"] else "manual"
                if cell["status"] == "stale":
                    summary[t]["stale"].append(dict(cluster=name, reason=cell["reason"], path=cell["path"]))
            elif k in ("mce", "hosted", "upi"):
                elsewhere = any(name in o for ot, o in tenants.items() if ot != t)
                has_src = day1 is not None and k != "upi"
                if (name in day1) if has_src else elsewhere:
                    miss = t in r.get("expected", [])
                    st = "missing" if miss else "available"
                    cell.update(status=st, path=fpath(k, r["site"], r["env"], r["mce"], name, create=True))
                    if miss:
                        cell["reason"] = "day1 registered this cluster for you (copy_to_sigs) but the folder doesn't exist; the copy Job may have failed"
                    if k == "hosted" and r["mce"] not in cl:
                        cell["reason"] = (cell["reason"] + ". " if miss else "") + f"tenant also lacks parent MCE folder {r['mce']}"
                    summary[t][st].append(dict(cluster=name, reason=cell["reason"], path=cell["path"]))
            r["cells"][t] = cell
    order = lambda r: ((0, r["site"], r["env"], r["mce"], r["kind"] != "mce", r["name"]) if r["kind"] in ("mce", "hosted")
                       else (1, r["site"], r["env"], "", 0, r["name"]) if r["kind"] == "upi" else (2, "", "", "", 0, ""))
    out = dict(rows=sorted(rows.values(), key=order), conflicts=[], summary=summary,
                expected_on=any(r.get("expected") for r in rows.values()),  # day1 registers at least one cluster with a tenant
                banner=None if day1 is not None else NO_DAY1)
    if day1 is not None:  # in day1 but no tenant repo has a folder for it (any cell with a folder counts, stale included)
        gap = {r["name"] for r in out["rows"] if r["source"] == "day1" and not any(c["path"] and c["status"] in ("onboarded", "stale") for c in r["cells"].values())}
        out["not_in_day2"] = [not_in_day2(r, r["mce"] in gap) for r in out["rows"] if r["name"] in gap]
    return out


def not_in_day2(r, mce_gap):
    ex = r.get("expected") or []
    why = (f"day1 registers it for {', '.join(ex)}, but none of their repos has the folder. The copy Job likely failed." if ex
           else "No tenant has a folder, so nothing from day2 is deployed to this cluster.")
    if r["kind"] == "hosted" and mce_gap:
        why += f" Its MCE {r['mce']} is not in day2 either."
    return dict(cluster=r["name"], kind=r["kind"], site=r["site"], env=r["env"], mce=r["mce"], version=r["version"], expected=ex,
                reason=why, paths={t: fpath(r["kind"], r["site"], r["env"], r["mce"], r["name"], create=True) for t in ex})


# ---------- provenance: which files feed each chart on each cluster ----------
def stacks(c, name, ch, v):
    """-> (deploy, values): [(label, repo path)] lowest -> highest, mirroring the platform templates:
    deploy = operators.yaml `valueFiles` (4 lines), values = deployApp.yaml `valueFiles` (non-hub / hub branches)."""
    k, s, e = c["kind"], c["site"], c["env"]
    if k == "hub":  # inClusterAppset.yaml valueFiles + deployApp.yaml `.Values.hub` branch
        return ([("Team default", f"operators/{ch}/{ch}.yaml"), ("Fleet default (hub)", f"defaults/hub/{ch}/{ch}.yaml")],
                [("Hub-wide", "defaults/hub/values.yaml"), ("Team default", f"operators/{ch}/values.yaml"),
                 ("Fleet default (hub)", f"defaults/hub/{ch}/values.yaml")])
    d, pin = f"defaults/{SCOPE[k]}/{ch}/", f"operators/{ch}/ocp-versions/{v}/"
    mce = f"sites/{s}/{e}/mces/{c['mce']}"
    cp = {"mce": f"{mce}/in-cluster", "hosted": f"{mce}/{name}", "upi": f"sites/{s}/{e}/upi/{name}"}[k]
    fleet = f"Fleet default ({SCOPE_LABEL[k]})"
    D = [("Team default", f"operators/{ch}/{ch}.yaml")] + ([(f"OCP {v} pin", f"{pin}{ch}.yaml")] if v else []) + \
        [(fleet, f"{d}{ch}.yaml"), ("This cluster's chart", f"{cp}/{ch}/{ch}.yaml")]
    V = [("Team default", f"operators/{ch}/values.yaml")] + ([(f"OCP {v} pin", f"{pin}values.yaml")] if v else []) + \
        [(f"Site {s}", f"sites/{s}/values.yaml"), (f"Site {s} \u00b7 {e}", f"sites/{s}/{e}/values.yaml"),
         (fleet, f"{d}values.yaml"), (f"Fleet default for {e}", f"{d}values-{e}.yaml"),
         ("Fleet default for this cluster", f"{d}values-{name}.yaml"),
         ("UPI-wide" if k == "upi" else "MCE-wide", f"sites/{s}/{e}/upi/values.yaml" if k == "upi" else f"{mce}/values.yaml"),
         ("Cluster-wide", f"{cp}/values.yaml"), ("This cluster's chart", f"{cp}/{ch}/values.yaml")]
    return D, V


def flat(d, pre=""):
    """Leaf key paths of a parsed YAML mapping: maps recurse, lists/scalars/{} are leaves."""
    out = {}
    for k, v in d.items():
        out.update(flat(v, f"{pre}{k}.") if isinstance(v, dict) and v else {f"{pre}{k}": v})
    return out


def layer(root, rel, cache):
    """None if the file is absent, else {leaves, e: parse error|None}. Reading is silent: errors are shown on the layer."""
    if rel not in cache:
        p, L = os.path.join(root, rel), None
        if os.path.isfile(p):
            L = dict(leaves={}, e=None)
            try:
                d = yaml.safe_load(open(p))
                if isinstance(d, dict):
                    L["leaves"] = flat(d)
                elif d is not None:
                    L["e"] = f"expected key/value content, got {type(d).__name__}"
            except Exception:
                L["e"] = "not valid YAML"
        cache[rel] = L
    return cache[rel]


def merge(layers):
    """Helm's merge over leaf paths, lowest -> highest: maps deep-merge, lists/scalars replaced, later wins.
    -> {key: [value, index of the layer that set it, indexes of earlier layers it overrode]}"""
    eff = {}
    for i, lv in enumerate(layers):
        for p, v in (lv or {}).items():
            below = [x for x in eff if x.startswith(p + ".")]
            if v == {} and below:  # an empty map merges into nothing
                continue
            parts = p.split(".")
            hit = [x for x in [p] + below + [".".join(parts[:j]) for j in range(1, len(parts))] if x in eff]
            over = sorted({o for x in hit for o in [eff[x][1]] + eff[x][2]})
            for x in hit:
                del eff[x]
            eff[p] = [v, i, over]
    return eff


def shown(p, v):
    if OPT["hide"]:
        return None
    if SECRET.search(p):
        return HIDDEN
    return (v if isinstance(v, str) else json.dumps(v, default=str))[:200]


def facts(de, D, root, ch, v, ladders, V, vl):
    """Summary + warnings for one chart on one cluster, from the raw deploy-config merge `de`."""
    g = lambda k: (de.get(k) or [None])[0]
    lab = lambda k: D[de[k][1]][0] if k in de else None
    rev = g("targetRevision")
    auto = [k for k, x in de.items() if k.startswith("syncPolicy.automated") and (k != "syncPolicy.automated" or x[0] not in (None, False))]
    hv = lambda x: None if OPT["hide"] else x
    s = dict(repo=hv(g("repourl")), path=hv(g("path") or "."), ns=hv(g("projectNamespace")), rev=hv(rev or "HEAD"),
             by=(lab("targetRevision") if rev else None) or "template default", sync="auto" if auto else "manual",
             prune=bool(g("syncPolicy.automated.prune")) if auto else None)
    w = []
    pin = next((i for i, (l, _) in enumerate(D) if l.startswith("OCP ")), None)
    fl = next(i for i, (l, _) in enumerate(D) if l.startswith("Fleet"))
    if pin is not None and "targetRevision" in de and de["targetRevision"][1] == fl and "targetRevision" in (ladders[pin] or {}).get("leaves", {}):
        w.append(dict(k="defaults-overrides-pin", t=f"{D[fl][0]} sets targetRevision above the {D[pin][0]}, so the pin is ignored. Remove targetRevision from {D[fl][1]}."))
    vers = dirs(f"{root}/operators/{ch}/ocp-versions")
    if v and vers and v not in vers:
        w.append(dict(k="missing-version-layer", t=f"No OCP {v} layer: the pinned chart falls back to the team default for {v} (pins exist for {', '.join(vers)}). Create operators/{ch}/ocp-versions/{v}/."))
    bad = [k for k in de if k.lower() == "repourl" and k != "repourl"]
    if bad and "repourl" not in de:
        w.append(dict(k="repourl-casing", t=f"Deploy config sets {bad[0]} but the template reads repourl (all lowercase): this renders an empty repoURL. Set by {lab(bad[0])}."))
    w += [dict(k="parse-error", t=f"{l} ({p}): {L['e']}; ignored.") for (l, p), L in zip(D + V, ladders + vl) if L and L["e"]]
    return s, w


def norm(x):
    """Repo URL: lowercase, no trailing slash, no trailing .git."""
    x = str(x or "").strip().lower().rstrip("/")
    return (x[:-4] if x.endswith(".git") else x).rstrip("/")


def npath(x):
    """Chart path: no leading ./, no trailing slash; '.' and '' are equal. Case is kept: paths are case-sensitive."""
    x = str(x or "").strip()
    while x.startswith("./"):
        x = x[2:]
    return "" if x.rstrip("/") == "." else x.rstrip("/")


def detect(cluster, ents):
    """ents: [(entry, source key|None)] of one cluster (all tenants, own and default) -> conflicts.
    conflict: >=2 entries, same repo+path AND same effective revision (unset = HEAD, literally).
    version-skew: >=2 entries, same repo+path, different revisions. A mixed group yields both. Path is part of the source: one repo can hold several charts."""
    out, by_src, hide = [], {}, OPT["hide"]
    for e, src in ents:
        if src:
            by_src.setdefault(src, []).append(e)
    show = lambda r: "HEAD (repo default branch)" if r == "HEAD" else r
    def mk(kind, es, why):
        es = sorted(es, key=lambda e: (e["tenant"], e["chart"]))
        if hide:  # keys and origins only: no repo, path or revision values in the page
            es = [dict(e, repourl=None, path=None, rev=None) for e in es]
        return dict(cluster=cluster, kind=kind, reason=why, entries=es, tenants=sorted({e["tenant"] for e in es}))
    for es in by_src.values():
        repo, revs = es[0]["repourl"], sorted({e["rev"] for e in es})
        for r in revs:
            same = [e for e in es if e["rev"] == r]
            if len(same) > 1:
                out.append(mk("conflict", same, f"The same chart source at the same revision is deployed {len(same)} times on this cluster; one owner is enough." if hide else
                              f"The same chart from {repo} at {show(r)} is deployed {len(same)} times on this cluster; one owner is enough."))
        if len(revs) > 1:
            out.append(mk("version-skew", es, f"Same chart source at {len(revs)} different revisions on one cluster; it should be one owner at one version." if hide else
                          f"Same chart from {repo} at {len(revs)} revisions ({', '.join(show(r) for r in revs)}) on one cluster; it should be one owner at one version."))
    return out


def add_prov(m, tenants, roots):
    """Adds cell['pv'] {chart: provenance}, cell['avail'], cell['excluded'], m['eff'] / m['files'] tables, m['sites'], m['catalog'] and m['conflicts']."""
    E, F, S, ents, M = {}, {}, {}, {}, {}
    eid = lambda e: E.setdefault(json.dumps(e, sort_keys=True), str(len(E)))
    out = lambda e: {k: [shown(k, x[0]), x[1], x[2]] for k, x in e.items()}
    for t, cl in tenants.items():
        root, cache = roots[t], {}
        cat = {}  # the tenant's catalog: operators/<chart>/ -> (team default targetRevision, {ocp version: pin targetRevision})
        for ch in dirs(f"{root}/operators"):
            rev = lambda p: (layer(root, p, cache) or dict(leaves={}))["leaves"].get("targetRevision")
            cat[ch] = (rev(f"operators/{ch}/{ch}.yaml"), {v: rev(f"operators/{ch}/ocp-versions/{v}/{ch}.yaml") for v in dirs(f"{root}/operators/{ch}/ocp-versions")})
        M[t] = len(cat)
        for r in m["rows"]:
            c, cell = cl.get(r["name"]), r["cells"][t]
            if cell["status"] in ("onboarded", "stale", "available", "missing") and not (c and c["kind"] == "hub"):
                have = {x["name"] for x in cell["charts"]} | set(c["excluded"] if c else [])
                base = cell["path"] + ("in-cluster/" if c and c["kind"] == "mce" else "")
                cell["avail"] = [dict(chart=ch, rev=None if cat[ch][0] is None else shown("targetRevision", cat[ch][0]), pinned=r["version"] in cat[ch][1],
                                      pin=None if cat[ch][1].get(r["version"]) is None else shown("targetRevision", cat[ch][1][r["version"]]), folder=f"{base}{ch}/")
                                 for ch in cat if ch not in have]
            if not c or cell["status"] not in ("onboarded", "stale"):
                continue
            cell["excluded"] = [dict(chart=x, scope=SCOPE[c["kind"]], via=f"defaults/{SCOPE[c['kind']]}/exclusions.yaml") for x in c["excluded"]] if c["kind"] != "hub" else []
            cell["pv"] = {}
            for chd in cell["charts"]:
                ch = chd["name"]
                D, V = stacks(c, r["name"], ch, r["version"] if c["kind"] != "hub" else None)
                dl, vl = [layer(root, p, cache) for _, p in D], [layer(root, p, cache) for _, p in V]
                for (_, p), L in zip(D + V, dl + vl):
                    if L:
                        F.setdefault(t, {})[p] = dict(n=len(L["leaves"]), **({"e": L["e"]} if L["e"] else {}))
                de = merge([L and L["leaves"] for L in dl])
                s, w = facts(de, D, root, ch, r["version"] if c["kind"] != "hub" else None, dl, V, vl)
                g = lambda k: (de.get(k) or [None])[0]
                rp, pa, rev = g("repourl"), npath(g("path")), str(g("targetRevision") or "HEAD")
                cell["pv"][ch] = dict(d=D, v=V, de=eid(out(de)), ve=eid(out(merge([L and L["leaves"] for L in vl]))), s=s, w=w)
                ents.setdefault(r["name"], []).append((dict(tenant=t, chart=ch, origin=chd["origin"], repourl=rp, path=pa, rev=rev,
                                                           by=D[de["targetRevision"][1]][0] if "targetRevision" in de else "template default"), (norm(rp), pa) if norm(rp) else None))
        for site in dirs(f"{root}/sites"):  # what sites/<site>/**/values.yaml feed, per tenant
            files = []
            for env, rel in [(None, f"sites/{site}/values.yaml")] + [(e, f"sites/{site}/{e}/values.yaml") for e in dirs(f"{root}/sites/{site}")]:
                L = layer(root, rel, cache)
                cs = [c for c in cl.values() if c["site"] == site and (env is None or c["env"] == env)]
                if L:  # only files that exist
                    files.append(dict(p=rel, env=env, n=len(L["leaves"]), e=L["e"], k=sorted(L["leaves"]),
                                      ch=sum(len(c["charts"]) for c in cs), cl=len(cs)))
            if files:
                S.setdefault(site, {})[t] = files
    m["conflicts"] = [x for r in m["rows"] for x in sorted(detect(r["name"], ents.get(r["name"], [])), key=lambda x: x["kind"] != "conflict")]
    m["conflict_counts"] = {k: sum(x["kind"] == k for x in m["conflicts"]) for k in ("conflict", "version-skew")}
    m["catalog"] = M
    m["eff"], m["files"], m["sites"], m["hide_values"] = {i: json.loads(j) for j, i in E.items()}, F, S, OPT["hide"]


# ---------- output ----------
def cell_md(c):
    n = len(c["charts"])
    return {"none": "-", "available": "available", "missing": "MISSING", "stale": f"stale ({n})",
            "onboarded": f"unverified ({n})" if c["reason"].startswith("unverified") else f"onboarded ({n})"}[c["status"]]


def render_md(m, tenants):
    L = ["# Fleet Map", "", f"Generated {m['generated']}", ""]
    if m["banner"]:
        L += [f"> **{m['banner']}**", ""]
    L += ["| cluster | kind | site | env | mce | ocp | source | " + " | ".join(tenants) + " |",
          "|" + "---|" * (7 + len(tenants))]
    for r in m["rows"]:
        L.append("| " + " | ".join([r["name"], r["kind"], r["site"], r["env"], r["mce"], r["version"] or "", r["source"]]
                                   + [cell_md(r["cells"][t]) for t in tenants]) + " |")
    for t in tenants:
        s = m["summary"][t]
        L += ["", f"## {t}", "", "Stale - remove:"] + [f"- {x['cluster']}: {x['reason']}" for x in s["stale"]] * bool(s["stale"])
        if not s["stale"]:
            L.append("- none")
        if m["expected_on"]:
            L += ["", "Missing - day1 registered it, create the folder:"] + [f"- {x['cluster']}: {x['path']}" for x in s["missing"]] * bool(s["missing"])
            if not s["missing"]:
                L.append("- none")
        L += ["", "Available - add:"] + [f"- {x['cluster']}" + (f" ({x['reason']})" if x["reason"] else "") for x in s["available"]]
        if not s["available"]:
            L.append("- none")
    if m.get("not_in_day2"):
        L += ["", "## In day1, not in day2", ""] + [f"- {x['cluster']} ({x['kind']}): {x['reason']}" for x in m["not_in_day2"]]
    if m["warnings"]:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in m["warnings"]]
    L += ["", "## Conflicts", ""]
    L += [f"- {c['cluster']} [{c['kind']}]: " + ", ".join(f"{x['tenant']}/{x['chart']} ({x['origin']})" for x in c["entries"]) for c in m["conflicts"]] or ["- none"]
    return "\n".join(L) + "\n"


def atomic(path, text):
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def hide(args):
    return bool(args.hide_values or os.environ.get("HIDE_VALUES", "").lower() == "true")


def make_model(trees, day1_dir, repos, hidden):
    """trees: {tenant: directory}. -> the model dict (the one thing the page, inventory.json and INVENTORY.md are made from)."""
    with MODEL_LOCK:
        WARNINGS.clear()
        OPT["hide"] = hidden
        names = sorted(trees)
        tenants = {n: scan_tenant(trees[n], n) for n in names}
        day1 = scan_day1(day1_dir) if day1_dir else None
        m = build(tenants, day1)
        add_prov(m, tenants, trees)
        m["generated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        m["tenants"] = names
        m["warnings"] = list(WARNINGS)
        m["repos"] = repos  # commit per project for the page footer; empty with --sigs-dir
        return m


def generate(args, f=None):
    """f: a Fetcher (GitLab mode) or None (--sigs-dir). Returns False when nothing changed since the last output."""
    sigs, day1_dir, shas = f.sync() if f else (args.sigs_dir, args.day1_dir, {})
    sig = [shas, f.names()] if f else shas  # a new or deleted branch changes the picker, so it counts as a change
    if f and sig == f.last and os.path.exists(f"{args.out}/index.html"):
        log(f"no changes ({len(shas)} projects at the same commits)")
        return False
    names = dirs(sigs)
    m = make_model({n: os.path.join(sigs, n) for n in names}, day1_dir, shas, hide(args))
    os.makedirs(args.out, exist_ok=True)
    atomic(f"{args.out}/inventory.json", json.dumps(m, indent=1))
    atomic(f"{args.out}/INVENTORY.md", render_md(m, names))
    page = dict(m, live=True, branches=f.names(), refs=f.default_refs()) if f and getattr(args, "cmd", None) == "serve" else m  # live: /api/view exists
    data = json.dumps(page).replace("<", "\\u003c")
    atomic(f"{args.out}/index.html", open(f"{HERE}/index.tmpl.html").read().replace("__DATA__", data))
    if f:
        f.last = sig
    log(f"generated {len(m['rows'])} clusters x {len(names)} tenants, {m['conflict_counts']['conflict']} conflicts, {m['conflict_counts']['version-skew']} version warnings")
    return True


def view(args, f, refs):
    """The model for one branch combination (refs: ['tenant:branch', ...]); tenants not listed stay on their default branch."""
    with VIEW_LOCK:
        want = f.pick(refs)
        names, day1 = dirs(f"{f.cache}/sigs"), f.state.get("day1")
        off = {t: w for t, w in want.items() if not w["default"] and f.state.get(f"sigs/{t}", {}).get("sha") != w["sha"]}  # needs its own tree
        shas = {t: off[t]["sha"] if t in off else f.state[f"sigs/{t}"]["sha"] for t in names if t in off or f"sigs/{t}" in f.state}
        if len(shas) < len(names):
            raise ViewError("No data yet: some tenant repositories are not fetched.", 503, 5)
        key = (tuple(sorted(shas.items())), day1 and day1["sha"])
        m = VIEWS.get(key)
        hit, dl0, label = m is not None, f.views_dl, " ".join(f"{t}@{w['branch']}" for t, w in sorted(want.items())) or "defaults"
        if m is None:
            trees = {}
            for n, t in enumerate(sorted(off)):
                if n:
                    time.sleep(STAGGER)
                trees[t] = f.tree_for(t, off[t])
            f.evict(set(trees.values()))
            trees = {t: trees.get(t) or f"{f.cache}/sigs/{t}" for t in names}
            repos = {(f"{t}@{off[t]['branch']}" if t in off else t): sha for t, sha in shas.items()}
            if day1:
                repos["(day1)"] = day1["sha"]
            m = make_model(trees, f"{f.cache}/day1" if f.day1 else None, repos, hide(args))
            VIEWS[key] = m
            while len(VIEWS) > MAX_VIEWS:
                VIEWS.popitem(last=False)
        VIEWS.move_to_end(key)
        log(f"view {label}: {f.views_dl - dl0} archive downloads, {'model cached' if hit else 'rendered'}")
        dflt = f.default_refs()
        refs_out = {t: dict(branch=want[t]["branch"], sha=shas[t], default=want[t]["default"]) if t in want else dflt[t] for t in shas if t in want or t in dflt}
        return dict(m, refs=refs_out, branches=f.names(), live=True)


def prefetch(args, f):
    """PREFETCH_REFS: keep these views rendered; bad entries are logged and skipped."""
    ok = []
    for r in f.prefetch:
        try:
            f.pick([r])
            ok.append(r)
        except ViewError as e:
            log(f"warn: PREFETCH_REFS {r}: {e}")
    if ok:
        view(args, f, ok)


def make_server(args, f=None):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=args.out, **k)

        def reply(self, code, text, *hdr):
            self.send_response(code); self.send_header("Content-Length", str(len(text)))
            for h in hdr:
                self.send_header(*h)
            self.end_headers(); self.wfile.write(text)

        def api_view(self, qs):
            js = lambda code, o, *hdr: self.reply(code, json.dumps(o).encode(), ("Content-Type", "application/json"), ("Cache-Control", "no-store"), *hdr)
            if not f:
                return js(404, dict(error="branch views need GitLab mode (SIGS_URL)"))
            try:
                js(200, view(args, f, [v for k, v in urllib.parse.parse_qsl(qs, keep_blank_values=True) if k == "ref"]))
            except ViewError as e:
                js(e.code, dict(error=str(e), **({"retry_after": e.retry} if e.retry else {})), *([("Retry-After", str(e.retry))] if e.retry else []))
            except Exception as e:
                log(Redactor.clean(f"view failed: {e!r}"))
                js(500, dict(error="internal error, see the pod logs"))

        def list_directory(self, path):  # never show a file listing (an empty /data/out looked like a broken page)
            self.send_error(404)

        def do_GET(self):
            if self.path == "/healthz":  # liveness only: stays ok while GitLab is down
                self.reply(200, b"ok")
            elif self.path.split("?")[0] == "/api/view":
                self.api_view(self.path.partition("?")[2])
            elif self.path.split("?")[0] in ("/", "/index.html") and not os.path.exists(f"{args.out}/index.html"):
                self.reply(503, b"No data yet: the first fetch from GitLab has not succeeded. Retrying; see the pod logs.\n",
                           ("Content-Type", "text/plain"), ("Retry-After", str(min(args.interval, 60))))
            else:
                super().do_GET()

    os.makedirs(args.out, exist_ok=True)
    return http.server.ThreadingHTTPServer(("", args.port), H)


def serve(args, f=None):
    def loop():
        while True:
            try:
                generate(args, f)
                if f:
                    prefetch(args, f)
            except BaseException as e:  # keep last good output
                log(Redactor.clean(f"generate failed (keeping last output): {e}"))
            time.sleep(args.interval)

    srv = make_server(args, f)
    threading.Thread(target=loop, daemon=True).start()
    srv.serve_forever()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["generate", "serve"])
    p.add_argument("--out", required=True)
    p.add_argument("--sigs-dir")
    p.add_argument("--day1-dir")
    p.add_argument("--hide-values", action="store_true", help="keys and origins only; no values in the page (also HIDE_VALUES=true)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL") or 60), help="seconds between regenerations (also INTERVAL)")
    a = p.parse_args()
    try:
        f = None if a.sigs_dir else Fetcher(os.environ)
    except ConfigError as e:
        log(f"config error: {e}")
        sys.exit(2)
    if f:
        f.interval = a.interval
    if a.cmd == "serve":
        serve(a, f)
    else:
        try:
            generate(a, f)
        except Exception as e:
            log(Redactor.clean(f"error: {e}"))
            sys.exit(1)


if __name__ == "__main__":
    main()
