#!/usr/bin/env python3
"""Checks against the LIVE page. Usage: live_check.py static|idle|mutate|outage [BASE_URL]
static: seeded statuses/warnings/provenance, UI (Playwright), no secrets in served files, screenshots.
idle: with no commits, every cycle costs only the project list + one branch call per project, and zero archive downloads.
mutate: day1/tenant changes through the GitLab API must show up on the page within ~2 intervals (and back after reverting).
outage: GitLab down + pod restart: no crash loop, /healthz ok, recovery."""
import json, os, subprocess, sys, time, urllib.error, urllib.request

E = os.path.dirname(os.path.abspath(__file__))
S = f"{E}/.state"
mode = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:18080"
GL = "http://127.0.0.1:8929"
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))
tok = lambda n: open(f"{S}/{n}.token").read().strip()
fails = []
INTERVAL = 60


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def get(path, base=BASE):
    try:
        with OPEN.open(base + path, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def inv():
    c, b = get("/inventory.json")
    return json.loads(b) if c == 200 else None


def row(m, n): return next(r for r in m["rows"] if r["name"] == n)
def cell(m, n, t): return row(m, n)["cells"][t]


def commit(project, actions, msg):
    req = urllib.request.Request(f"{GL}/api/v4/projects/{project.replace('/', '%2F')}/repository/commits", method="POST",
                                 data=json.dumps(dict(branch="main", commit_message=msg, actions=actions)).encode(),
                                 headers={"PRIVATE-TOKEN": tok("root"), "Content-Type": "application/json"})
    OPEN.open(req, timeout=60).read()


def ctl(**kw):
    req = urllib.request.Request(f"{GL}/__ctl", method="POST", data=json.dumps(kw).encode(), headers={"PRIVATE-TOKEN": tok("root")})
    return json.load(OPEN.open(req, timeout=15))["log"]


def glog(): return ctl()
def n_of(log, s): return sum(1 for e in log if e["p"].split("?")[0].endswith(s))


def kubectl(*a): return subprocess.run(["k3s", "kubectl", *a], capture_output=True, text=True).stdout.strip()


# ------------------------------------------------------------------ static
def static():
    m = inv()
    ok(m is not None and len(m["rows"]) == 28 and m["tenants"] == ["a", "b", "c"], "live inventory.json: 28 clusters x tenants a b c")
    ok(m["banner"] is None and m["warnings"] == [] and m["expected_on"], "day1 is read (no banner), no unreadable files, copy_to_sigs seen")
    st = lambda n, t: cell(m, n, t)["status"]
    ok(st("ocp4-prod-hc08-site1", "a") == "missing" and st("ocp4-prod-hc08-site1", "b") == "missing" and st("ocp4-prod-hc08-site1", "c") == "available", "global [a,b]: hc08 is missing for a and b, available for c")
    ok(st("ocp4-prod-hc01-site4", "c") == "onboarded" and cell(m, "ocp4-prod-hc01-site4", "c")["reg"] == "day1" and st("ocp4-prod-hc04-site4", "c") == "missing" and st("ocp4-prod-hc01-site4", "a") == "available", "site4 MCE narrowed to [c]: hc01 onboarded via day1, hc04 missing, a has hc01 available")
    ok(st("ocp4-prod-hc09-site1", "a") == "onboarded" and cell(m, "ocp4-prod-hc09-site1", "a")["reg"] == "manual" and st("ocp4-prod-hc09-site1", "b") == "available", "cluster file copy_to_sigs [] : a's folder is manual, b is available")
    ok(st("ocp4-upgrade-demo", "a") == "available" and st("ocp4-mce-lab", "a") == "available" and row(m, "ocp4-upgrade-demo")["env"] == "", "env-less real names are in the fleet with no env, nothing registered for them")
    ok(st("ocp4-prod-hc03-site1", "a") == "stale" and cell(m, "ocp4-prod-hc03-site1", "a")["reason"] == "not in day1", "a: folder for a cluster not in day1 is stale")
    ok(st("ocp4-prep-hc02-site1", "b") == "stale" and "env prep" in cell(m, "ocp4-prep-hc02-site1", "b")["reason"] and st("ocp4-prep-mce-site1-a", "b") == "stale", "b: prep cluster and MCE filed under prod are stale (env mismatch)")
    ok(st("ocp4-prod-hc05-site1", "c") == "stale" and "site site4" in cell(m, "ocp4-prod-hc05-site1", "c")["reason"], "c: hc05 under the site4 site is stale (moved)")
    sm = lambda t, k: sorted(x["cluster"] for x in m["summary"][t][k])
    ok(sm("a", "missing") == ["ocp4-prep-hc01-site1", "ocp4-prep-hc03-site1", "ocp4-prod-hc04-site5", "ocp4-prod-hc08-site1"] and sm("a", "stale") == ["ocp4-prod-hc03-site1"], "a: summary lists 4 missing, 1 stale")
    ok(len(sm("b", "missing")) == 9 and sm("c", "missing") == ["ocp4-prod-hc04-site4", "ocp4-prod-hc05-site4"], "b has 9 missing, c has 2 missing")
    nid = {x["cluster"]: x for x in m["not_in_day2"]}
    ok(sorted(nid) == sorted(["ocp4-mce-lab", "ocp4-prep-hc01-site1", "ocp4-prep-hc03-site1", "ocp4-prod-hc04-site5", "ocp4-prod-hc08-site1", "ocp4-prod-hc04-site4", "ocp4-prod-hc05-site4", "ocp4-upgrade-demo"]), "not_in_day2: the 8 day1 clusters no tenant repo has a folder for")
    ok(nid["ocp4-prod-hc08-site1"]["expected"] == ["a", "b"] and "copy Job likely failed" in nid["ocp4-prod-hc08-site1"]["reason"] and nid["ocp4-prod-hc04-site4"]["expected"] == ["c"], "not_in_day2: expected tenants and the copy-Job reason")
    ok(nid["ocp4-upgrade-demo"]["expected"] == [] and nid["ocp4-upgrade-demo"]["reason"].startswith("No tenant has a folder") and "mce-lab is not in day2" in nid["ocp4-upgrade-demo"]["reason"] and nid["ocp4-upgrade-demo"]["paths"] == {}, "not_in_day2: nobody expected (env-less names), MCE gap mentioned")
    ok("ocp4-prod-hc09-site1" not in nid and "ocp4-prod-hc03-site1" not in nid and "ocp4-prep-hc02-site1" not in nid, "not_in_day2: manual, stale and env-mismatch folders count as having a folder")
    cf = {(c["cluster"], c["kind"]): sorted((x["tenant"], x["chart"], x["rev"]) for x in c["entries"]) for c in m["conflicts"]}
    ok(cf.get(("ocp4-prod-hc05-site1", "conflict")) == [("a", "kyverno", "release-4.16.27"), ("b", "kyverno-dup", "release-4.16.27")] and ("ocp4-prod-hc05-site1", "version-skew") not in cf, "b's kyverno-dup is a's kyverno source at the same effective revision (a's OCP pin) on hc05: conflict")
    hs = [sorted((x["tenant"], x["rev"]) for x in c["entries"]) for c in m["conflicts"] if c["cluster"] == "ocp4-prod-hc04-site1" and c["kind"] == "version-skew" and c["entries"][0]["chart"] == "kyverno"]
    ok(hs == [[("a", "release-4.16.27"), ("b", "main")]] and ("ocp4-prod-hc04-site1", "conflict") not in cf, "a (pinned) and b (main) deploy kyverno from one source on hc04: version-skew, not a conflict")
    ok(m["conflict_counts"]["conflict"] >= 1 and m["conflict_counts"]["version-skew"] >= 1, "conflict counts by kind")
    d = cell(m, "ocp4-upi01-site1", "a")
    ok(d["status"] == "onboarded" and d["reason"].startswith("unverified") and row(m, "ocp4-upi01-site1")["version"] == "4.16.27", "UPI cluster with version.yaml: unverified, version read")
    ok(".yml" in row(m, "ocp4-prep-hc03-site1")["warns"][0] and cell(m, "ocp4-prep-hc03-site1", "c")["status"] == "available", ".yml cluster is read and warned about")
    ok(row(m, "ocp4-upgrade-demo")["workers"] == ["4.21.39"] and row(m, "ocp4-prod-hc08-site1")["workers"] == ["4.16.27"] and row(m, "ocp4-prod-hc08-site1")["version"] == "4.16.30", "mid-upgrade clusters: worker tags differ from mastertag")
    pv = lambda n, t, ch: cell(m, n, t)["pv"][ch]
    kinds = lambda x: [w["k"] for w in x["w"]]
    ok(kinds(pv("ocp4-prod-hc01-site1", "a", "logging")) == ["defaults-overrides-pin"], "a/hc01: defaults targetRevision overrides the 4.16.27 pin")
    ok(kinds(pv("ocp4-prep-hc02-site1", "a", "kyverno")) == ["missing-version-layer"], "a/hc02 on 4.20.9: no pin layer")
    ok(kinds(pv("ocp4-prod-hc04-site1", "a", "cluster-roles")) == ["repourl-casing"], "a/hc04 cluster-roles: repoUrl casing bug flagged")
    ok(pv("ocp4-prod-hc01-site1", "a", "kyverno")["s"]["by"] == "OCP 4.16.27 pin", "pin layer selected for a 4.16.27 cluster")
    ok([e["chart"] for e in cell(m, "ocp4-prod-hc05-site1", "a")["excluded"]] == ["logging"], "exclusions.yaml: logging not applied on hc05")
    ok("prod-hub" in [r["name"] for r in m["rows"]] and cell(m, "prod-hub", "a")["charts"], "defaults/hub shows as the hub row")
    et = pv("ocp4-prod-hc04-site1", "a", "example-token")
    ok(m["eff"][et["ve"]]["api.password"][0] == "••• (hidden)" and m["eff"][et["ve"]]["api.token"][0] == "••• (hidden)", "secret-ish keys are hidden in the effective values")
    ok({x["env"] for x in m["sites"]["site1"]["a"]} >= {None, "prod"}, "site/env values files seen (site1 and site1/prod)")
    ok(m["repos"]["(day1)"] and all(m["repos"][t] for t in "abc"), "all four repos were fetched (commit shas recorded)")

    # served files never carry the tokens or secret values
    served = "".join(get(p)[1] for p in ("/", "/inventory.json", "/INVENTORY.md"))
    ok(tok("read") not in served and tok("root") not in served, "neither token appears in served HTML/JSON/Markdown")
    ok("hunter2" not in served and "not-for-the-page" not in served, "secret values never reach the page")
    logs = kubectl("logs", "deploy/gitops-fleet-map", "--tail=-1")
    ok(logs and tok("read") not in logs and tok("root") not in logs, "tokens are not in the pod logs")
    ok("token: scopes read_api, read_repository" in logs and "config: GitLab http://" in logs and "4 projects" in logs, "pod log has the token scopes/expiry line and the config summary")
    ok(not any(tok("read") in e["p"] for e in glog()), "token is not in any request URL the GitLab saw")
    sec = kubectl("get", "pod", "-l", "app=gitops-fleet-map", "-o", "jsonpath={.items[0].spec.containers[0].securityContext.runAsUser} {.items[0].spec.containers[0].securityContext.readOnlyRootFilesystem}")
    ok(sec == "1000700000 true", f"pod runs as 1000700000 with a read-only root ({sec})")
    ok(get("/healthz") == (200, "ok"), "/healthz ok")
    ui()


def ui():
    from playwright.sync_api import sync_playwright
    os.makedirs(f"{E}/shots", exist_ok=True)
    shot = lambda pg, n: pg.screenshot(path=f"{E}/shots/{n}.png")
    with sync_playwright() as p:
        br = p.chromium.launch()

        def page(w=1440, h=900):
            pg = br.new_context(viewport={"width": w, "height": h}).new_page()
            pg.errs = []
            pg.on("console", lambda m: pg.errs.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: pg.errs.append(str(e)))
            return pg

        pg = page(); pg.goto(BASE + "/#tab=clusters"); pg.wait_for_selector("tr.r")
        ok(pg.inner_text("#count") == "Showing 28 of 28 clusters" and not pg.errs, "live page renders all 28 clusters, no console errors")
        ok(pg.locator(".tile .top").all_inner_texts() == ["Onboarded", "Missing", "Stale", "Available", "Conflicts", "Not in day2"], "live page has the Missing tile")
        pg.wait_for_timeout(300); shot(pg, "1-fleet-view")
        m = inv(); sites = sorted({r["site"] for r in m["rows"] if r["site"]})
        ok([x.split("\n")[0] for x in pg.locator("#sitelist .ni").all_inner_texts()] == ["All sites"] + sites + ["Management hub"], f"live sidebar: All sites, {', '.join(sites)}, Management hub")
        for k in sites:
            n = sum(1 for r in m["rows"] if r["site"] == k and r["kind"] != "hub")
            pg.click(f"#sitelist .ni[data-site={k}]")
            ok(pg.inner_text("#count") == f"Showing {n} of {n} clusters" and pg.inner_text("#ctx-t") == k and pg.locator("tr.r").count() == n, f"live: site {k} scopes the matrix to its {n} clusters")
        pg.click("#sitelist .ni[data-site=site4]"); pg.select_option("#tenant", "c"); pg.click("#tab-settings")
        ok(pg.evaluate("location.hash") == "#site=site4&tenant=c&tab=settings" and pg.inner_text("#settings").strip() == "No site-level values at site4.", "live: Site settings page and hash for site4 / tenant c (no site-level files: one neutral line)")
        pg.click("#tab-actions"); t = pg.inner_text("#area")
        ok("ocp4-prod-hc04-site4" in t and "ocp4-prod-hc03-site1" not in t, "live: site4 / tenant c action items exclude other sites")
        pg.goto(BASE + "/#tab=clusters"); pg.reload(); pg.wait_for_selector("tr.r")
        pg.goto(BASE + "/#tenant=c"); pg.reload(); pg.wait_for_selector(".acts li")
        t = pg.inner_text("#area")
        ok("Missing: create these folders (2)" in t and "ocp4-prod-hc04-site4" in t and "ocp4-prod-hc05-site4" in t and "Stale: remove these folders (1)" in t, "tenant c action items: 2 missing, 1 stale")
        ok("sites/site4/prod/mces/ocp4-prod-mce-site4-a/ocp4-prod-hc04-site4/" in t, "tenant c: path to create for hc04")
        pg.wait_for_timeout(300); shot(pg, "2-tenant-c-actions")
        pg.goto(BASE + "/#tenant=a&tab=clusters"); pg.reload(); pg.wait_for_selector("tr.r")
        pg.locator("tr.r:has(.name:text-is('ocp4-prod-hc08-site1')) td.nm button.cb").click(); pg.wait_for_timeout(500)
        s = pg.inner_text("#sheet")
        ok("workers on 4.16.27" in s and "upgrade in progress" in s and "Day1 registers this cluster with: a, b." in s, "cluster sheet: upgrade in progress, who day1 registers it with")
        shot(pg, "3-cluster-sheet-upgrade")
        pg.keyboard.press("Escape")
        pg.locator("tr.r:has(.name:text-is('ocp4-prep-hc03-site1')) td.nm button.cb").click(); pg.wait_for_timeout(400)
        ok("render will fail" in pg.inner_text("#sheet"), "cluster sheet: .yml warning")
        pg.keyboard.press("Escape")
        pg.locator("[data-k='[\"ocp4-prod-hc01-site1\",\"a\"]']").click(); pg.wait_for_timeout(400)
        pg.locator("#sheet li:has(> button.chh .cn:text-is('logging')) button.chh").click()
        ok("sets targetRevision above the OCP 4.16.27 pin" in pg.inner_text("#sheet"), "chart provenance: defaults override the pin")
        ok(not pg.errs, "no console errors across the live interactions")
        shot_site = page(); shot_site.goto(BASE + "/#site=site4&tenant=c"); shot_site.reload(); shot_site.wait_for_selector(".acts li"); shot_site.wait_for_timeout(300); shot(shot_site, "2b-site4-tenant-c-actions")
        m = page(390, 844); m.goto(BASE + "/#tenant=c"); m.reload(); m.wait_for_selector(".acts li"); m.wait_for_timeout(300)
        ok(m.evaluate("document.documentElement.scrollWidth") <= 390, "mobile: no horizontal scroll")
        shot(m, "4-mobile-tenant-c")
        ok(m.locator("#sitelist .ni").count() == len(sites) + 2 and m.evaluate("document.documentElement.scrollWidth") <= 390, "mobile: site chip row, no horizontal scroll")
        m.goto(BASE + "/#tab=clusters"); m.reload(); m.wait_for_selector("tr.r"); m.locator("td.cell button.cb").first.click(); m.wait_for_timeout(500)
        shot(m, "5-mobile-sheet")
        br.close()


# ------------------------------------------------------------------ idle
def idle():
    """Wait for two quiet cycles; count what the GitLab saw."""
    m0 = inv()
    ctl(clear_log=True)
    time.sleep(INTERVAL * 2 + 10)
    log = glog()
    gens = inv()["generated"]
    n_cyc = sum(1 for e in log if "/groups/" in e["p"])
    br, arc = n_of(log, "/repository/branches/main"), n_of(log, "/repository/archive.tar.gz")
    print(f"idle window {INTERVAL * 2 + 10}s: {len(log)} requests = {n_cyc} project lists + {br} branch checks + {arc} archive downloads + {len(log) - n_cyc - br - arc} other", flush=True)
    ok(arc == 0, "idle: ZERO archive downloads")
    ok(n_cyc >= 2 and len(log) == 5 * n_cyc, f"idle: each cycle = 1 project list + day1 branch check + 3 tenant branch lists ({n_cyc} cycles, {len(log)} requests)")
    ok(gens == m0["generated"], "idle: page not regenerated")
    logs = kubectl("logs", "deploy/gitops-fleet-map", "--tail=6")
    ok("no changes" in logs, "idle: pod log says 'no changes'")


# ------------------------------------------------------------------ mutate
D1, B_REPO = "org/day1/platform-config", "org/day2/sigs/b"
HC04 = "sites/site1/mces/ocp4-prod-mce-site1-a/hostedClusters/ocp4-prod-hc04-site1.yaml"
NEWC = "sites/site4/mces/ocp4-prod-mce-site4-a/hostedClusters/ocp4-prod-newest-site4.yaml"
BGAL = "sites/site4/prod/mces/ocp4-prod-mce-site4-a/ocp4-prod-hc01-site4/kyverno/.gitkeep"


def wait_for(conds, limit):
    """conds {name: fn(model)->bool}; returns {name: seconds since call} for those that became true."""
    t0, seen = time.time(), {}
    while time.time() - t0 < limit and len(seen) < len(conds):
        m = inv()
        for n, f in conds.items():
            if n not in seen and m and f(m):
                seen[n] = round(time.time() - t0, 1)
        time.sleep(3)
    return seen


def mutate():
    m = inv()
    base_gen = m["generated"]
    ok(cell(m, "ocp4-prod-hc04-site1", "a")["status"] == "onboarded" and "ocp4-prod-newest-site4" not in [r["name"] for r in m["rows"]] and cell(m, "ocp4-prod-hc01-site4", "b")["status"] == "available", "baseline: hc04 onboarded, newest absent, hc01 available for b")
    herzi_raw = OPEN.open(urllib.request.Request(f"{GL}/api/v4/projects/{D1.replace('/', '%2F')}/repository/files/{HC04.replace('/', '%2F')}/raw?ref=main", headers={"PRIVATE-TOKEN": tok("root")})).read().decode()
    ctl(clear_log=True)
    commit(D1, [dict(action="delete", file_path=HC04)], "mutation a: delete hc04")
    commit(D1, [dict(action="create", file_path=NEWC, content="mastertag: 4.16.27-x86_64\nday2:\n  copy_to_sigs: [c]\n")], "mutation b: add newest-site4 for c")
    commit(B_REPO, [dict(action="create", file_path=BGAL, content="")], "mutation c: b adds hc01")
    print(f"mutations committed (interval {INTERVAL}s); polling", flush=True)
    conds = {
        "a: hc04 stale for a, b, c": lambda m: all(cell(m, "ocp4-prod-hc04-site1", t)["status"] == "stale" and cell(m, "ocp4-prod-hc04-site1", t)["reason"] == "not in day1" for t in "abc"),
        "b: newest missing for c": lambda m: "ocp4-prod-newest-site4" in [r["name"] for r in m["rows"]] and cell(m, "ocp4-prod-newest-site4", "c")["status"] == "missing",
        "c: hc01 onboarded for b": lambda m: cell(m, "ocp4-prod-hc01-site4", "b")["status"] == "onboarded",
    }
    seen = wait_for(conds, 3 * INTERVAL + 30)
    for n in conds:
        ok(n in seen and seen[n] <= 2 * INTERVAL + 15, f"{n}: visible after {seen.get(n, 'never')}s (limit {2 * INTERVAL + 15}s)")
    arc = n_of(glog(), "/repository/archive.tar.gz")
    print(f"archive downloads for 3 commits in 2 projects (day1, b): {arc}", flush=True)
    ok(2 <= arc <= 4, f"only the changed projects were downloaded ({arc} archives, expected 2)")
    m = inv()
    ok(cell(m, "ocp4-prod-newest-site4", "a")["status"] == "available" and cell(m, "ocp4-prod-hc01-site4", "b").get("reg") == "manual", "side effects: newest is available for a; b's hc01 folder is a hand-added one")
    ok(m["generated"] != base_gen, "page was regenerated")
    # the UI shows it too
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(); pg = br.new_page(); pg.goto(BASE + "/#tenant=c"); pg.wait_for_selector(".acts li")
        t = pg.inner_text("#area")
        ok("Missing: create these folders (3)" in t and "ocp4-prod-newest-site4" in t, "UI: tenant c now has 3 missing including newest-site4")
        br.close()
    # revert, and check it propagates back
    commit(D1, [dict(action="create", file_path=HC04, content=herzi_raw), dict(action="delete", file_path=NEWC)], "revert a, b")
    commit(B_REPO, [dict(action="delete", file_path=BGAL)], "revert c")
    back = {
        "hc04 back": lambda m: cell(m, "ocp4-prod-hc04-site1", "a")["status"] == "onboarded",
        "newest removed": lambda m: "ocp4-prod-newest-site4" not in [r["name"] for r in m["rows"]],
        "hc01 available for b": lambda m: cell(m, "ocp4-prod-hc01-site4", "b")["status"] == "available",
    }
    seen = wait_for(back, 3 * INTERVAL + 30)
    for n in back:
        ok(n in seen and seen[n] <= 2 * INTERVAL + 15, f"revert, {n}: after {seen.get(n, 'never')}s")


# ------------------------------------------------------------------ outage
def outage():
    gl = lambda a: subprocess.run(["sh", f"{E}/gitlab.sh", a], check=True)
    restarts = lambda: kubectl("get", "pod", "-l", "app=gitops-fleet-map", "-o", "jsonpath={.items[-1].status.containerStatuses[0].restartCount}")
    before = inv()
    gl("stop")
    print("GitLab stopped; waiting 1.5 intervals with the pod running", flush=True)
    time.sleep(INTERVAL * 1.5)
    after = inv()
    ok(after is not None and after["generated"] == before["generated"], "GitLab down, pod running: page keeps the last good output")
    logs = kubectl("logs", "deploy/gitops-fleet-map", "--tail=20")
    ok("generate failed (keeping last output)" in logs, "failure is logged ('keeping last output')")
    ok(tok("read") not in logs, "token not in the failure log")
    # restart the pod while GitLab is down
    pods = lambda: [x.split() for x in kubectl("get", "pod", "-l", "app=gitops-fleet-map", "-o", r'jsonpath={range .items[*]}{.metadata.name} {.status.containerStatuses[0].ready}{"\n"}{end}').splitlines()]
    old = pods()[0][0]
    kubectl("delete", "pod", old, "--wait=false")
    obs, t0, ready = [], time.time(), False
    while time.time() - t0 < 90 and not ready:
        ready = any(n != old and r == "true" for n, r in pods())
        time.sleep(2)
    ok(ready, f"new pod becomes Ready while GitLab is down ({round(time.time() - t0)}s)")
    time.sleep(5)  # the supervised port-forward in up.sh reconnects to the new pod
    # sample the pod itself (not the port-forward, which flaps while the pod is replaced) for ~1.5 intervals
    probe = "import urllib.request as u,urllib.error as e\nfor p in ('/healthz','/','/inventory.json'):\n try: r=u.urlopen('http://localhost:8080'+p); print(r.status, r.read(60).decode().replace(chr(10),' '))\n except e.HTTPError as x: print(x.code, x.read(60).decode().replace(chr(10),' '))"
    for i in range(6):
        out = kubectl("exec", next(n for n, r in pods() if n != old), "--", "python3", "-c", probe).splitlines()
        obs.append((int(out[0][:3]), int(out[1][:3]), int(out[2][:3]), out[1][4:]))
        time.sleep(15)
    print("samples (healthz, /, inventory.json, body start):", obs, flush=True)
    ok(all(o[0] == 200 for o in obs), "/healthz stays 200 during the outage")
    ok(all(o[1] == 503 and "No data yet" in o[3] and o[2] == 404 for o in obs), "restarted pod without GitLab: / is a 503 'No data yet' (no directory listing), inventory.json 404")
    ok(restarts() == "0", f"no crash loop (restartCount {restarts()})")
    print("PAGE DURING OUTAGE after restart:", obs[-1][1], obs[-1][2], repr(obs[-1][3]), flush=True)
    gl("start")
    t1 = time.time()
    seen = None
    while time.time() - t1 < 3 * INTERVAL and seen is None:
        m = inv()
        if m: seen = round(time.time() - t1, 1)
        time.sleep(3)
    ok(seen is not None, f"page recovers after GitLab is back ({seen}s)")
    m = inv()
    ok(m is not None and len(m["rows"]) == 28, "recovered page has the full fleet")
    ok(restarts() == "0", "still no restarts")


{"static": static, "idle": idle, "mutate": mutate, "outage": outage}[mode]()
print(f"\n{mode}: {len(fails)} FAILED" if fails else f"\n{mode}: all passed")
sys.exit(1 if fails else 0)
