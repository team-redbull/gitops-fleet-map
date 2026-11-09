#!/usr/bin/env python3
"""Minimal GitLab stand-in for the e2e run (the real gitlab-ce image cannot be pulled here: Docker Hub is blocked).
Implements only what the inventory tool and seed.py use: groups/projects REST (pagination via X-Next-Page, include_subgroups),
PATs with scopes + expiry, commits API (create/update/delete), tree, raw files, branches (list + single), repository archive (git archive, named as GitLab does),
/version, /-/readiness, and git smart HTTP (clone + push).
Usage: fake_gitlab.py DATA_DIR PORT ROOT_TOKEN READ_TOKEN [URL_PREFIX]   (URL_PREFIX e.g. /gitlab: serve everything under that sub-path)
Test controls, root token only: GET /__ctl -> {"log": [{m, p, code}]}; POST /__ctl with any of
  {"archive429": <Retry-After secs>|null, "evil": {"path": <project path>, "kind": dotdot|abs|symlink|device}|null,
   "read_scopes": [...], "read_expires": "YYYY-MM-DD"|null, "clear_log": true}"""
import base64, datetime, gzip, http.server, io, json, os, re, subprocess, sys, tarfile, tempfile, threading, urllib.parse

DATA, PORT, ROOT_TOKEN, READ_TOKEN = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
PREFIX = (sys.argv[5] if len(sys.argv) > 5 else "").rstrip("/")
CTL = {"archive429": None, "evil": None, "read_expires": (datetime.date.today() + datetime.timedelta(days=365)).isoformat(), "log": []}
REPOS, STATE = os.path.join(DATA, "repos"), os.path.join(DATA, "state.json")
LOCK = threading.RLock()
os.makedirs(REPOS, exist_ok=True)
S = json.load(open(STATE)) if os.path.exists(STATE) else {"groups": {}, "projects": {}, "next": 1}  # id -> {path,...}
TOKENS = {ROOT_TOKEN: ["api", "read_api", "read_repository", "write_repository"], READ_TOKEN: ["read_api", "read_repository"]}


def save():
    json.dump(S, open(STATE, "w"))


def nid():
    S["next"] += 1
    return S["next"] - 1


def git(*a, cwd=None, inp=None):
    r = subprocess.run(["git", *a], cwd=cwd, input=inp, capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="e2e", GIT_AUTHOR_EMAIL="e2e@example.com", GIT_COMMITTER_NAME="e2e", GIT_COMMITTER_EMAIL="e2e@example.com"))
    if r.returncode:
        raise RuntimeError(f"git {a}: {r.stderr.strip()}")
    return r.stdout


def proj(ref):  # numeric id or url-encoded path
    ref = urllib.parse.unquote(str(ref))
    return next((p for p in S["projects"].values() if str(p["id"]) == ref or p["path_with_namespace"] == ref), None)


def bare(p):
    return os.path.join(REPOS, p["path_with_namespace"] + ".git")


def commit(p, body):
    """Commits API on a temp clone of the bare repo."""
    br = body.get("branch", "main")
    with tempfile.TemporaryDirectory() as w:
        git("clone", "-q", bare(p), w + "/c")
        c = w + "/c"
        if subprocess.run(["git", "rev-parse", "--verify", "-q", "origin/" + br], cwd=c, capture_output=True).returncode == 0:
            git("checkout", "-q", "-B", br, "origin/" + br, cwd=c)
        else:
            git("checkout", "-q", "-B", br, cwd=c)
        for a in body["actions"]:
            f = os.path.join(c, a["file_path"])
            if a["action"] == "delete":
                os.remove(f)
                continue
            if a["action"] == "create" and os.path.exists(f):
                raise ValueError(f"A file with this name already exists: {a['file_path']}")
            os.makedirs(os.path.dirname(f), exist_ok=True)
            data = base64.b64decode(a["content"]) if a.get("encoding") == "base64" else a["content"].encode()
            open(f, "wb").write(data)
        git("add", "-A", cwd=c)
        git("commit", "-q", "--allow-empty", "-m", body.get("commit_message", "e2e"), cwd=c)
        git("push", "-q", "origin", f"{br}:{br}", cwd=c)
        return git("rev-parse", "HEAD", cwd=c).strip()


def archive(p, sha):
    """tar.gz of the commit with GitLab's top-level dir name; CTL['evil'] swaps in a hostile archive for one project."""
    top = f"{p['path']}-{sha}-{sha}/"
    ev = CTL["evil"]
    if ev and ev["path"] == p["path_with_namespace"]:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            def add(name, typ=tarfile.REGTYPE, link=""):
                ti = tarfile.TarInfo(name); ti.type, ti.linkname = typ, link
                data = b"x: 1\n" if typ == tarfile.REGTYPE else b""
                ti.size = len(data); t.addfile(ti, io.BytesIO(data))
            add(top + "ok.yaml")
            k = ev["kind"]
            if k == "dotdot": add("../escaped.yaml")
            elif k == "abs": add("/tmp/escaped-abs.yaml")
            elif k == "symlink": add(top + "link", tarfile.SYMTYPE, "../../../../etc")
            elif k == "device": add(top + "dev", tarfile.CHRTYPE)
        return gzip.compress(buf.getvalue())
    tar = subprocess.run(["git", "archive", "--format=tar", f"--prefix={top}", sha], cwd=bare(p), capture_output=True, check=True).stdout
    return gzip.compress(tar)


def pj(p):
    return dict(id=p["id"], name=p["path"], path=p["path"], path_with_namespace=p["path_with_namespace"], default_branch="main")


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    log_message = lambda *a: None

    def send(self, code, body=b"", ctype="application/json", extra=()):
        if not self.path.startswith(PREFIX + "/__ctl"):
            CTL["log"].append({"m": self.command, "p": self.path[len(PREFIX):], "code": code})
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        t = self.headers.get("PRIVATE-TOKEN")
        a = self.headers.get("Authorization", "")
        if not t and a.startswith("Basic "):
            t = base64.b64decode(a[6:]).decode().partition(":")[2]
        return t if t in TOKENS else None

    def do_GET(self): self.route(b"")
    def do_POST(self): self.route(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
    def do_DELETE(self): self.route(b"")

    def ctl(self, body):
        if self.token() != ROOT_TOKEN:
            return self.send(401, {"message": "401 Unauthorized"})
        j = json.loads(body) if body else {}
        for k in ("archive429", "evil", "read_expires"):
            if k in j: CTL[k] = j[k]
        if "read_scopes" in j: TOKENS[READ_TOKEN] = j["read_scopes"]
        if j.get("clear_log"): CTL["log"].clear()
        self.send(200, {"log": CTL["log"]})

    def route(self, body):
        path, _, qs = self.path.partition("?")
        if PREFIX:
            if path != PREFIX and not path.startswith(PREFIX + "/"):
                return self.send(404, b"<html>not found</html>", "text/html")
            path = path[len(PREFIX):]
        if path == "/__ctl":
            return self.ctl(body)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}
        try:
            if path == "/-/readiness" or path == "/-/health":
                return self.send(200, {"status": "ok"})
            tok = self.token()
            if not tok:
                return self.send(401, {"message": "401 Unauthorized"}, extra=[("WWW-Authenticate", 'Basic realm="GitLab"')])
            sc = TOKENS[tok]
            if path.startswith("/api/v4/"):
                with LOCK:
                    return self.api(path[8:], q, body, sc)
            return self.git(path, qs, body, sc)
        except Exception as e:
            self.send(500, {"message": str(e)})

    def need(self, sc, s):
        if s not in sc:
            self.send(403, {"error": "insufficient_scope", "error_description": f"requires {s}"})
            return False
        return True

    def api(self, path, q, body, sc):
        m = self.command
        j = json.loads(body) if body else {}
        if path == "version":
            return self.send(200, {"version": "17.0.0", "revision": "fake"})
        if path == "personal_access_tokens/self":
            return self.send(200, {"id": 1, "name": "fake", "scopes": sc, "active": True, "revoked": False,
                                   "expires_at": CTL["read_expires"] if self.token() == READ_TOKEN else None})
        if path == "groups" and m == "POST":
            if not self.need(sc, "api"): return
            parent = S["groups"].get(str(j.get("parent_id")))
            full = (parent["full_path"] + "/" if parent else "") + j["path"]
            g = next((x for x in S["groups"].values() if x["full_path"] == full), None)
            if g: return self.send(409, {"message": "Group already exists"})
            g = dict(id=nid(), full_path=full, name=j.get("name", j["path"]), path=j["path"])
            S["groups"][str(g["id"])] = g; save()
            return self.send(201, g)
        mm = re.fullmatch(r"groups/([^/]+)", path)
        if mm and m == "GET":
            ref = urllib.parse.unquote(mm[1])
            g = next((x for x in S["groups"].values() if str(x["id"]) == ref or x["full_path"] == ref), None)
            return self.send(200, g) if g else self.send(404, {"message": "404 Group Not Found"})
        mm = re.fullmatch(r"groups/([^/]+)/projects", path)
        if mm and m == "GET":
            if not self.need(sc, "read_api"): return
            ref = urllib.parse.unquote(mm[1])
            g = next((x for x in S["groups"].values() if str(x["id"]) == ref or x["full_path"] == ref), None)
            if not g: return self.send(404, {"message": "404 Group Not Found"})
            sub = q.get("include_subgroups") == "true"
            ps = sorted((pj(p) for p in S["projects"].values()
                         if p["path_with_namespace"].startswith(g["full_path"] + "/") and (sub or "/" not in p["path_with_namespace"][len(g["full_path"]) + 1:])), key=lambda x: x["id"])
            pp, pg = int(q.get("per_page", 20)), int(q.get("page", 1))
            nxt = pg + 1 if pg * pp < len(ps) else ""
            return self.send(200, ps[(pg - 1) * pp:pg * pp], extra=[("X-Next-Page", str(nxt)), ("X-Total", str(len(ps)))])
        if path == "projects" and m == "POST":
            if not self.need(sc, "api"): return
            g = S["groups"].get(str(j["namespace_id"]))
            full = g["full_path"] + "/" + j["path"]
            if proj(full): return self.send(409, {"message": "Project already exists"})
            p = dict(id=nid(), path=j["path"], path_with_namespace=full)
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare(p)], check=True)
            git("config", "http.receivepack", "true", cwd=bare(p))
            S["projects"][str(p["id"])] = p; save()
            return self.send(201, pj(p))
        mm = re.fullmatch(r"projects/([^/]+)(?:/(.*))?", path)
        if mm:
            p = proj(mm[1])
            if not p: return self.send(404, {"message": "404 Project Not Found"})
            sub = mm[2] or ""
            if sub == "" and m == "GET": return self.send(200, pj(p))
            if sub == "" and m == "DELETE":
                if not self.need(sc, "api"): return
                del S["projects"][str(p["id"])]; save()
                return self.send(202, {"message": "202 Accepted"})
            if sub == "repository/branches" and m == "GET":  # all branches with commit.id, offset pagination like GitLab 17.5
                if not self.need(sc, "read_api"): return
                out = git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", cwd=bare(p)).split("\n")
                bs = sorted((dict(name=l.split()[0], commit=dict(id=l.split()[1]), default=l.split()[0] == "main") for l in out if l), key=lambda b: b["name"])
                pp, pg = int(q.get("per_page", 20)), int(q.get("page", 1))
                nxt = pg + 1 if pg * pp < len(bs) else ""
                return self.send(200, bs[(pg - 1) * pp:pg * pp], extra=[("X-Next-Page", str(nxt)), ("X-Total", str(len(bs)))])
            mb = re.fullmatch(r"repository/branches/(.+)", sub)
            if mb and m == "GET":
                if not self.need(sc, "read_api"): return
                br = urllib.parse.unquote(mb[1])
                try:
                    sha = git("rev-parse", "--verify", "-q", "refs/heads/" + br, cwd=bare(p)).strip()
                except RuntimeError:
                    return self.send(404, {"message": "404 Branch Not Found"})
                return self.send(200, {"name": br, "commit": {"id": sha}, "default": br == "main"})
            if sub == "repository/archive.tar.gz" and m == "GET":
                if not self.need(sc, "read_api"): return
                if CTL["archive429"] is not None:
                    return self.send(429, "Retry later\n".encode(), "text/plain", [("Retry-After", str(CTL["archive429"]))])
                return self.send(200, archive(p, q["sha"]), "application/gzip")
            if sub == "repository/commits" and m == "POST":
                if not self.need(sc, "api"): return
                return self.send(201, {"id": commit(p, j)})
            if sub == "repository/tree" and m == "GET":
                if not self.need(sc, "read_api"): return
                try:
                    out = git("ls-tree", "-r", "--name-only", q.get("ref", "main"), cwd=bare(p)).split("\n")
                except RuntimeError:  # empty repo
                    out = []
                return self.send(200, [{"path": f, "type": "blob"} for f in out if f])
            mf = re.fullmatch(r"repository/files/([^/]+)/raw", sub)
            if mf and m == "GET":
                if not self.need(sc, "read_api"): return
                return self.send(200, git("show", f"{q.get('ref', 'main')}:{urllib.parse.unquote(mf[1])}", cwd=bare(p)).encode(), "text/plain")
        self.send(404, {"message": "404 Not Found: " + path})

    def git(self, path, qs, body, sc):
        mm = re.fullmatch(r"/(.+?\.git)/(.*)", path)
        if not mm: return self.send(404, b"not found", "text/plain")
        write = "git-receive-pack" in path or "service=git-receive-pack" in qs
        if not self.need(sc, "write_repository" if write else "read_repository"): return
        env = dict(os.environ, GIT_PROJECT_ROOT=REPOS, GIT_HTTP_EXPORT_ALL="1", PATH_INFO=path, QUERY_STRING=qs, REQUEST_METHOD=self.command,
                   CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(len(body)), REMOTE_USER="x", REMOTE_ADDR="127.0.0.1")
        if self.headers.get("Content-Encoding"): env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        r = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True)
        hd, _, b = r.stdout.partition(b"\r\n\r\n")
        code, hs = 200, []
        for l in hd.split(b"\r\n"):
            k, _, v = l.decode().partition(": ")
            if k.lower() == "status": code = int(v.split()[0])
            elif k.lower() not in ("content-length", "connection"): hs.append((k, v))
        self.send_response(code)
        for k, v in hs: self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
