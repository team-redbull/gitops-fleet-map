#!/usr/bin/env python3
"""Fetch layer against the fake GitLab (e2e/fake_gitlab.py, local process, no k3s): URL parsing, token, SHA checks, archives, rate limit, hostile archives.
Run: python tests/fetch_test.py"""
import argparse, concurrent.futures, glob, json, os, secrets, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "*"
import fleetmap as inv

inv.STAGGER = 0
LOGS = []
inv.log = lambda *a: LOGS.append(" ".join(map(str, a)))
fails = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


T = tempfile.mkdtemp(prefix="fetch-test-")
ROOT_TOK, READ_TOK = "glpat-" + secrets.token_hex(8), "glpat-" + secrets.token_hex(8)
WRONG = "glpat-" + secrets.token_hex(8)
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))
procs = []
import atexit; atexit.register(lambda: [p.kill() for p in procs])


def start(prefix=""):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    d = tempfile.mkdtemp(dir=T)
    procs.append(subprocess.Popen([sys.executable, f"{ROOT}/e2e/fake_gitlab.py", d, str(port), ROOT_TOK, READ_TOK, prefix]))
    url = f"http://127.0.0.1:{port}{prefix}"
    for _ in range(50):
        try:
            OPEN.open(url + "/-/readiness", timeout=1).read(); break
        except Exception:
            time.sleep(0.2)
    DIRS[url] = d
    return url


DIRS = {}


def call(base, method, path, body=None, tok=ROOT_TOK):
    r = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"PRIVATE-TOKEN": tok, "Content-Type": "application/json"})
    try:
        return json.load(OPEN.open(r, timeout=60))
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return None
        raise


class GL:
    def __init__(self, prefix=""):
        self.url = start(prefix)
        self.n = 0
        self.api = lambda m, p, b=None: call(self.url, m, "/api/v4/" + p, b)
        org = self.api("POST", "groups", dict(name="org", path="org"))["id"]
        d2 = self.api("POST", "groups", dict(name="day2", path="day2", parent_id=org))["id"]
        sigs = self.api("POST", "groups", dict(name="sigs", path="sigs", parent_id=d2))["id"]
        d1 = self.api("POST", "groups", dict(name="day1", path="day1", parent_id=org))["id"]
        self.pid = {}
        for g, name in ((sigs, "t1"), (sigs, "t2"), (sigs, "t3"), (d1, "platform-config")):
            self.pid[name] = self.api("POST", "projects", dict(name=name, path=name, namespace_id=g))["id"]
            self.commit(name)

    def commit(self, name, path=None, branch="main"):
        self.n += 1
        return self.api("POST", f"projects/{self.pid[name]}/repository/commits",
                        dict(branch=branch, commit_message="c", actions=[dict(action="create", file_path=path or f"sites/site1/f{self.n}.yaml", content="a: 1\n")]))["id"]

    def ctl(self, **kw):
        return call(self.url, "POST", "/__ctl", kw)["log"]

    def clear(self):
        self.ctl(clear_log=True)

    def log(self):
        return call(self.url, "GET", "/__ctl")["log"]

    def count(self, suffix):
        return sum(1 for e in self.log() if e["p"].split("?")[0].endswith(suffix))


def fetcher(gl, **kw):
    env = dict(SIGS_URL=gl.url + "/org/day2/sigs", DAY1_URL=gl.url + "/org/day1/platform-config", GITLAB_TOKEN=READ_TOK,
               CACHE_DIR=tempfile.mkdtemp(dir=T), INTERVAL="60")
    env.update(kw)
    return inv.Fetcher({k: v for k, v in env.items() if v is not None})


def cycle(f, out):
    """One serve-loop iteration. -> regenerated?"""
    return inv.generate(argparse.Namespace(out=out, sigs_dir=None, day1_dir=None, hide_values=False), f)


def model(out):
    return json.load(open(out + "/inventory.json"))


def raises(fn, exc, *needles):
    try:
        fn()
    except exc as e:
        return all(n in str(e) for n in needles)
    return False


gl = GL()
gl2 = GL("/gitlab")

# --- URL parsing
sp = inv.split_url
ok(sp("https://h.example/a/b/sigs") == ("https://h.example", ["a", "b", "sigs"]), "URL: plain")
ok(sp("https://h.example/a/b/sigs/") == sp("https://h.example/a/b/sigs//") == sp("https://h.example/a/b/sigs/-/tree/main") == sp("https://h.example/a/b/sigs/-/") == sp("https://h.example/a/b/sigs/?x=1#y"), "URL: trailing slash, /-/tree/main, /-/, query")
ok(sp("https://h.example/a/b/proj.git")[1] == ["a", "b", "proj"], "URL: .git suffix")
ok(raises(lambda: sp("gitlab.example/a"), inv.ConfigError, "not a web URL"), "URL: no scheme is a config error")
f = fetcher(gl, SIGS_URL=gl.url + "/org/day2/sigs/-/tree/main/")
f.setup()
ok(f.base == gl.url and f.group == "org/day2/sigs" and f.day1_path == "org/day1/platform-config" and f.version == "17.0.0", "URL: root GitLab, group and day1 derived")
f = fetcher(gl2, SIGS_URL=gl2.url + "/org/day2/sigs/-/tree/main/", DAY1_URL=gl2.url + "/org/day1/platform-config/")
f.setup()
ok(f.base == gl2.url and f.prefix == ["gitlab"] and f.group == "org/day2/sigs" and f.day1_path == "org/day1/platform-config", "URL: GitLab under /gitlab found by probing from the longest prefix")
out2 = tempfile.mkdtemp(dir=T)
ok(cycle(f, out2) and model(out2)["tenants"] == ["t1", "t2", "t3"], "sub-path GitLab: full cycle works")
ok(raises(lambda: fetcher(gl2, SIGS_URL=gl2.url + "/org/day2/sigs", DAY1_URL=gl.url + "/org/day1/platform-config").setup(), inv.ConfigError, "same GitLab"), "DAY1_URL on another GitLab is refused")
f = fetcher(gl, SIGS_URL=None, DAY1_URL=None, GITLAB_URL=gl.url + "/", SIGS_GROUP="org/day2/sigs", DAY1_PROJECT="org/day1/platform-config")
ok(cycle(f, tempfile.mkdtemp(dir=T)) and f.day1_path == "org/day1/platform-config", "old GITLAB_URL + SIGS_GROUP + DAY1_PROJECT still work")

# --- token: file wins over env, trailing newline ignored; startup warnings
tf = os.path.join(T, "token")
open(tf, "w").write(READ_TOK + "\n")
f = fetcher(gl, GITLAB_TOKEN=WRONG, GITLAB_TOKEN_FILE=tf)
f.setup()
ok(f.token == READ_TOK and f.version, "token file wins over GITLAB_TOKEN")
ok(raises(lambda: fetcher(gl, GITLAB_TOKEN_FILE=tf + ".nope"), inv.ConfigError, "GITLAB_TOKEN_FILE"), "missing token file is a config error")
ok(raises(lambda: fetcher(gl, GITLAB_TOKEN=None), inv.ConfigError, "GITLAB_TOKEN"), "no token is a config error")
soon = time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400))
gl.ctl(read_scopes=["read_api"], read_expires=soon)
LOGS.clear(); fetcher(gl).setup()
ok(any("warn" in l and "read_repository" in l for l in LOGS) and any("warn" in l and "expires in 5 days" in l for l in LOGS), "missing scope and expiry within 14 days are warned about")
ok(any(l.startswith("token: scopes read_api; expires " + soon) for l in LOGS), "token scopes and expiry date are logged")
gl.ctl(read_expires="2020-01-01")
LOGS.clear(); fetcher(gl).setup()
ok(any("EXPIRED" in l for l in LOGS), "expired token is warned about")
gl.ctl(read_scopes=["read_api", "read_repository"], read_expires="2999-01-01")
LOGS.clear(); fetcher(gl).setup()
ok(not any("warn" in l for l in LOGS), "good token: no warning")
gl.ctl(read_scopes=["api"]); LOGS.clear(); fetcher(gl).setup()
ok(not any("warn" in l for l in LOGS), "'api' scope counts as read_api + read_repository")
gl.ctl(read_scopes=["read_api", "read_repository"])

# --- clear errors
ok(raises(lambda: fetcher(gl, GITLAB_TOKEN=WRONG).setup(), inv.GitlabError, "rejected the token (401)", "GITLAB_TOKEN"), "wrong token: clear 401 message")
ok(raises(lambda: fetcher(gl2, GITLAB_TOKEN=WRONG, SIGS_URL=gl2.url + "/org/day2/sigs").setup(), inv.GitlabError, "rejected the token (401)"), "wrong token under a sub-path: clear 401 message")
ok(raises(lambda: fetcher(gl, SIGS_URL=gl.url + "/org/day2/nope").sync(), inv.GitlabError, "SIGS_URL group not found or token can't see it", "Reporter"), "unknown group: actionable 404 message")
ok(raises(lambda: fetcher(gl, DAY1_URL=gl.url + "/org/day1/nope").sync(), inv.GitlabError, "DAY1_URL project not found"), "unknown day1 project: actionable 404 message")
ok(raises(lambda: fetcher(gl, SIGS_URL="https" + gl.url[4:] + "/org/day2/sigs").sync(), inv.GitlabError, "TLS", "GITLAB_CA_FILE"), "TLS error: points at GITLAB_CA_FILE")
ok(raises(lambda: fetcher(gl, SIGS_URL="http://127.0.0.1:9/org/day2/sigs").sync(), inv.GitlabError, "cannot reach 127.0.0.1:9"), "unreachable host: clear message")
ok(raises(lambda: fetcher(gl2, SIGS_URL=gl2.url[:-7] + "/org/day2/sigs").sync(), inv.GitlabError, "no GitLab API found", "/api/v4/version"), "URL without the GitLab sub-path: says no API found")
ok(raises(lambda: fetcher(gl, GITLAB_CA_FILE=tf), inv.ConfigError, "GITLAB_CA_FILE"), "bad CA file is a config error")

# --- cycles
out, f = tempfile.mkdtemp(dir=T), None
f = fetcher(gl)
gl.clear(); LOGS.clear()
ok(cycle(f, out) is True, "first run regenerates")
ok(gl.count("repository/archive.tar.gz") == 4 and gl.count("/repository/branches") == 3 and gl.count("/repository/branches/main") == 1, "first run: one branch list per tenant project, one branch check for day1, 4 archive downloads (3 tenants + day1)")
ok(any(l.startswith("config: GitLab " + gl.url) and "3 tenant projects" in l and "day1 on" in l and "interval 60s" in l for l in LOGS), "startup config summary line")
m = model(out)
ok(m["tenants"] == ["t1", "t2", "t3"] and set(m["repos"]) == {"t1", "t2", "t3", "(day1)"} and all(len(v) == 40 for v in m["repos"].values()), "output built from the archives, footer shas from the branches API")
gl.clear(); LOGS.clear(); gen = m["generated"]
ok(cycle(f, out) is False and any("no changes" in l for l in LOGS), "second run: no changes, no regenerate")
ok(gl.count("repository/archive.tar.gz") == 0 and len(gl.log()) == 5 and gl.count("/repository/branches") == 3, f"second run: ZERO archive requests; {len(gl.log())} API calls = 1 project list + 3 branch lists + 1 day1 branch check")
ok(model(out)["generated"] == gen, "output untouched when nothing changed")
f2 = fetcher(gl, CACHE_DIR=f.cache)  # restart with a warm cache
gl.clear()
ok(cycle(f2, out) is True and gl.count("repository/archive.tar.gz") == 0, "restart with the same cache dir: regenerates once, downloads nothing")

sha = gl.commit("t2")
gl.clear()
ok(cycle(f, out) is True and gl.count("repository/archive.tar.gz") == 1 and gl.count(f"projects/{gl.pid['t2']}/repository/archive.tar.gz") == 1, "one commit to one tenant: exactly one archive download, for that tenant")
ok(model(out)["repos"]["t2"] == sha and os.path.exists(f"{f.cache}/sigs/t2/sites/site1/f{gl.n}.yaml") and not glob.glob(f"{f.cache}/tmp*"), "new tree swapped in, top-level dir stripped, tmp cleaned")

gl.api("DELETE", f"projects/{gl.pid['t3']}")
gl.clear()
ok(cycle(f, out) is True and model(out)["tenants"] == ["t1", "t2"] and not os.path.exists(f"{f.cache}/sigs/t3") and gl.count("repository/archive.tar.gz") == 0 and "sigs/t3" not in f.state, "deleted project: dropped from output and cache, no download")

# --- rate limit: 429 keeps old data, pauses, recovers
old1 = model(out)["repos"]["t1"]
new1, new2 = gl.commit("t1"), gl.commit("t2")
gl.ctl(archive429=1); gl.clear(); LOGS.clear()
ok(cycle(f, out) is False, "429: cycle does not fail, nothing regenerated")
ok(model(out)["repos"]["t1"] == old1 and gl.count("repository/archive.tar.gz") == 1 and any("rate limited" in l for l in LOGS), "429: old data kept, only one archive attempt (the rest paused), logged")
gl.clear(); cycle(f, out)
ok(gl.count("repository/archive.tar.gz") == 0, "429: paused while Retry-After has not passed")
gl.ctl(archive429=None); time.sleep(1.2); gl.clear()
ok(cycle(f, out) is True and gl.count("repository/archive.tar.gz") == 2 and model(out)["repos"]["t1"] == new1 and model(out)["repos"]["t2"] == new2, "after Retry-After the next cycle downloads both and regenerates")

# --- hostile archives
for kind in ("dotdot", "abs", "symlink", "device"):
    n1, n2 = gl.commit("t1"), gl.commit("t2")
    gl.ctl(evil={"path": "org/day2/sigs/t1", "kind": kind}); gl.clear(); LOGS.clear()
    r = cycle(f, out)
    ok(r is True and model(out)["repos"]["t2"] == n2 and model(out)["repos"]["t1"] != n1 and any("t1: archive rejected" in l for l in LOGS), f"{kind}: archive rejected, cycle continues, other tenant updated, old t1 kept")
    ok(not os.path.exists("/tmp/escaped-abs.yaml") and not any(os.path.exists(os.path.join(d, "escaped.yaml")) for d in (f.cache, os.path.dirname(f.cache), f.cache + "/tmp")), f"{kind}: nothing written outside the tree")
    gl.clear(); cycle(f, out)
    ok(gl.count("repository/archive.tar.gz") == 0, f"{kind}: rejected commit is not downloaded again every cycle")
    gl.ctl(evil=None); gl.commit("t1"); gl.clear()
    ok(cycle(f, out) is True and gl.count("repository/archive.tar.gz") == 1, f"{kind}: next commit is fetched normally")
gl.ctl(evil={"path": "org/day2/sigs/t1", "kind": "symlink"})
gl.commit("t1")
ok(raises(lambda: fetcher(gl).sync(), inv.Wait, "waiting for sigs/t1"), "hostile archive and nothing cached: Wait (last output stays), no partial page")
gl.ctl(evil=None)

# --- branch views: one branch list per project per cycle, /api/view downloads only what is not cached
B = GL()
bare = lambda name: f"{DIRS[B.url]}/repos/org/day2/sigs/{name}.git"
for i in range(120):  # t3: 121 branches -> two pages of the branch list
    subprocess.run(["git", "-C", bare("t3"), "branch", f"feature/b{i:03d}", "main"], check=True)
B.commit("t1", "sites/site1/dev-only.yaml", branch="dev")
B.commit("t2", branch="rel")
bout, bargs = tempfile.mkdtemp(dir=T), None
bf = fetcher(B, MAX_CACHED_TREES="30")
bargs = argparse.Namespace(out=bout, sigs_dir=None, day1_dir=None, hide_values=False, cmd="serve", port=0, interval=60)
B.clear(); LOGS.clear()
ok(inv.generate(bargs, bf) is True, "views: first cycle generates")
ok(B.count("/repository/branches") == 4 and B.count("repository/archive.tar.gz") == 4, "views: branch list is one call per project (two for the 121-branch project, X-Next-Page followed); archives only for default branches")
ok(len(bf.brs["t3"]["br"]) == 121 and set(bf.brs["t1"]["br"]) == {"main", "dev"} and all(len(v) == 40 for e in bf.brs.values() for v in e["br"].values()), "views: {branch: sha} stored per tenant")
page = open(bout + "/index.html").read()
ok('"live": true' in page and '"branches": {"t1": ["dev", "main"]' in page and "branches" not in open(bout + "/inventory.json").read() and "dev" not in open(bout + "/INVENTORY.md").read(), "views: the page carries the branch lists and live flag; inventory.json and INVENTORY.md stay the default view only")
B.clear(); LOGS.clear()
ok(inv.generate(bargs, bf) is False and B.count("repository/archive.tar.gz") == 0 and B.count("/repository/branches") == 4, "views: a no-change cycle makes 0 archive downloads")
srv = inv.make_server(bargs, bf)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def api(qs):
    try:
        r = OPEN.open(f"http://127.0.0.1:{PORT}/api/view?{qs}", timeout=60)
        return r.status, json.load(r), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers


B.clear(); LOGS.clear()
c, v, _ = api("ref=t1:dev")
dev1 = bf.brs["t1"]["br"]["dev"]
ok(c == 200 and B.count("repository/archive.tar.gz") == 1 and any(l == f"view t1@dev: {dev1[:7]} downloading archive" for l in LOGS), "views: t1:dev downloads exactly 1 archive the first time and logs it")
ok(v["refs"]["t1"] == dict(branch="dev", sha=dev1, default=False) and v["refs"]["t2"]["default"] is True and v["refs"]["t2"]["branch"] == "main" and v["branches"]["t1"] == ["dev", "main"] and "t1@dev" in v["repos"] and v["live"] is True, "views: response has refs {branch, sha, default} for every tenant, branches and repos")
ok(os.path.isdir(f"{bf.cache}/trees/{B.pid['t1']}/{dev1}/sites/site1") and os.path.exists(f"{bf.cache}/trees/{B.pid['t1']}/{dev1}/sites/site1/dev-only.yaml"), "views: tree cached under trees/<project-id>/<sha>/")
B.clear(); api("ref=t1:dev")
ok(B.count("repository/archive.tar.gz") == 0 and len(B.log()) == 0, "views: the same view again: 0 downloads, no GitLab request at all (rendered model cached)")
inv.VIEWS.clear(); B.clear(); api("ref=t1:dev")
ok(B.count("repository/archive.tar.gz") == 0, "views: model cache empty, tree cache warm: still 0 downloads")
B.clear(); c, v, _ = api("ref=t1:main&ref=t2:main")
ok(c == 200 and B.count("repository/archive.tar.gz") == 0 and all(r["default"] for r in v["refs"].values()), "views: naming a default branch costs nothing")
B.clear(); api("ref=t1:dev&ref=t2:rel")
ok(B.count("repository/archive.tar.gz") == 1, "views: combination downloads only the ref not yet cached (t2:rel), at most one archive per non-default ref")

# new commit on dev: the next cycle sees the new sha, the next view downloads it
new_dev = B.commit("t1", branch="dev")
B.clear(); ok(inv.generate(bargs, bf) is False, "views: commit on a non-default branch does not regenerate the default page")
B.clear(); c, v, _ = api("ref=t1:dev")
ok(v["refs"]["t1"]["sha"] == new_dev and B.count("repository/archive.tar.gz") == 1 and B.count(f"projects/{B.pid['t1']}/repository/archive.tar.gz") == 1, "views: new commit on dev: the next view downloads it again")

# LRU: oldest non-default tree goes, default trees stay
trees = lambda: sorted(glob.glob(f"{bf.cache}/trees/*/*"), key=os.path.getmtime)
ok(len(trees()) == 3, "views: three non-default trees cached (dev old, dev new, rel)")
bf.max_trees = 2
api("ref=t1:dev&ref=t2:rel&ref=t3:feature/b001")  # (t3's branch is at main's commit: it reuses the default tree)
ok(len(trees()) == 2 and not os.path.isdir(f"{bf.cache}/trees/{B.pid['t1']}/{dev1}"), "views: LRU eviction drops the least recently used tree, never one in the current view")
bf.max_trees = 0; inv.VIEWS.clear(); api("ref=t1:dev")
ok(len(trees()) == 1 and all(os.path.isdir(f"{bf.cache}/sigs/{t}") for t in ("t1", "t2", "t3")) and os.path.isdir(f"{bf.cache}/day1"), "views: eviction keeps the default trees")
bf.max_trees = 30

# validation: never reaches GitLab
B.clear()
bad = ["ref=nope:dev", "ref=t1:nope", "ref=t1", "ref=:main", "ref=t1:../../x", "ref=t1:%2e%2e", "ref=t1:%2E%2E%2Fx", "ref=t1:main%3Fsha%3D", "ref=t1:main?sha=", "ref=t1:main/../dev",
       "ref=t1:dev&ref=t1:main", "ref=t1:dev%00", "ref=t1:" + "x" * 5000, "ref=" + "%2F..%2F" * 20, "ref=t1:ma%69n%20", "ref=t1:refs/heads/dev", "ref=../t1:dev"]
res = [api(q) for q in bad]
ok(all(r[0] == 400 and r[1]["error"] for r in res), "views: unknown tenant/branch, hostile names, duplicates: 400 with a message: " + "; ".join(f"{q[:24]}={r[0]}" for q, r in zip(bad, res) if r[0] != 400))
ok(res[1][1]["error"] == "tenant t1 has no branch 'nope'" and res[0][1]["error"].startswith("unknown tenant 'nope'"), "views: clear 400 messages")
ok(len(B.log()) == 0, f"views: hostile and unknown refs never reach GitLab ({len(B.log())} requests)")

# concurrent identical requests: one download
inv.VIEWS.clear(); shutil_rm = __import__("shutil").rmtree; shutil_rm(f"{bf.cache}/trees", ignore_errors=True)
B.clear()
with concurrent.futures.ThreadPoolExecutor(8) as ex:
    rs = list(ex.map(lambda _: api("ref=t2:rel"), range(8)))
ok(all(r[0] == 200 for r in rs) and B.count("repository/archive.tar.gz") == 1 and len({json.dumps(r[1]["rows"]) for r in rs}) == 1, "views: 8 concurrent identical requests cause a single download and one answer")

# rate limit on archive: 503 + Retry-After, nothing more asked while paused
B.commit("t2", branch="rel"); inv.generate(bargs, bf)
B.ctl(archive429=1); B.clear()
c, v, h = api("ref=t2:rel")
ok(c == 503 and h["Retry-After"] == "1" and "try again in 1s" in v["error"] and v["retry_after"] == 1 and B.count("repository/archive.tar.gz") == 1, "views: 429 on archive gives 503 with Retry-After and a message the UI can show")
B.clear(); c, v, h = api("ref=t2:rel")
ok(c == 503 and B.count("repository/archive.tar.gz") == 0 and "Retry-After" in h, "views: while paused, no further archive request")
B.ctl(archive429=None); time.sleep(1.2); B.clear()
ok(api("ref=t2:rel")[0] == 200 and B.count("repository/archive.tar.gz") == 1, "views: works again after Retry-After")

# PREFETCH_REFS: views kept warm by the cycle; bad entries are skipped with a warning
pf = fetcher(B, PREFETCH_REFS="t1:dev, t2:rel,zz:x")
inv.VIEWS.clear(); LOGS.clear(); B.clear()
inv.generate(argparse.Namespace(out=tempfile.mkdtemp(dir=T), sigs_dir=None, day1_dir=None, hide_values=False, cmd="serve"), pf); inv.prefetch(bargs, pf)
ok(B.count("repository/archive.tar.gz") == 6 and any("PREFETCH_REFS zz:x" in l for l in LOGS), "prefetch: default trees + the two listed refs downloaded, bad entry warned about")
srv.shutdown()
ok(raises(lambda: fetcher(B, MAX_CACHED_TREES="x"), inv.ConfigError, "MAX_CACHED_TREES"), "MAX_CACHED_TREES must be a number")

# --- the token never shows up
blob = "\n".join(LOGS)
for root, _, files in os.walk(T):
    for fn in files:
        if fn.endswith((".json", ".html", ".md", ".log")) or fn == "state.json":
            blob += open(os.path.join(root, fn), errors="ignore").read()
ok(READ_TOK not in blob and WRONG not in blob and ROOT_TOK not in "\n".join(LOGS), "token never appears in logs, output files or the cache state")
ok(not any(READ_TOK in e["p"] for e in gl.log()), "token is not in any request URL")

for p in procs:
    p.terminate()
print(f"\nfetch_test: {len(fails)} FAILED" if fails else "\nfetch_test: all passed")
sys.exit(1 if fails else 0)
