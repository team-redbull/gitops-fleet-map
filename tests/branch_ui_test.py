#!/usr/bin/env python3
"""Branch views end to end: fake GitLab + e2e/seed.py (tenant a has `dev`, c has `release`) + a live `fleetmap.py serve` + Playwright.
Usage: branch_ui_test.py STATIC_OUT_DIR   (a generated output dir with index.html: the page without a server, no Branches control)"""
import atexit, json, os, secrets, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "*"
STATIC = f"file://{sys.argv[1]}/index.html"
fails, procs = [], []
atexit.register(lambda: [p.kill() for p in procs])


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def wait(url, tries=100):
    for _ in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except Exception:
            time.sleep(0.3)
    raise SystemExit("did not come up: " + url)


T = tempfile.mkdtemp(prefix="branch-ui-")
RT, DT = "glpat-" + secrets.token_hex(8), "glpat-" + secrets.token_hex(8)
GP, SP = port(), port()
procs.append(subprocess.Popen([sys.executable, f"{ROOT}/e2e/fake_gitlab.py", f"{T}/gl", str(GP), RT, DT], stderr=subprocess.DEVNULL))
GL = f"http://127.0.0.1:{GP}"
wait(GL + "/-/readiness")
subprocess.run([sys.executable, f"{ROOT}/e2e/seed.py", GL, RT], check=True, stdout=subprocess.DEVNULL)
subprocess.run([sys.executable, f"{ROOT}/e2e/seed.py", GL, RT], check=True, stdout=subprocess.DEVNULL)  # idempotent
bare = f"{T}/gl/repos/org/day2/sigs/b.git"
for i in range(1, 21):  # tenant b gets 21 branches: the picker offers a filter above 15
    subprocess.run(["git", "-C", bare, "branch", f"feature/f{i:02d}", "main"], check=True)
open(f"{T}/token", "w").write(DT)
env = dict(os.environ, SIGS_URL=GL + "/org/day2/sigs", DAY1_URL=GL + "/org/day1/platform-config", GITLAB_TOKEN_FILE=f"{T}/token", CACHE_DIR=f"{T}/cache", INTERVAL="3600")
procs.append(subprocess.Popen([sys.executable, f"{ROOT}/fleetmap.py", "serve", "--out", f"{T}/out", "--port", str(SP)], env=env, stderr=open(f"{T}/serve.log", "w")))
U = f"http://127.0.0.1:{SP}/"
wait(U + "inventory.json")
get = lambda q: json.load(urllib.request.urlopen(U + "api/view?" + q, timeout=60))
base = json.loads(wait(U + "inventory.json"))
cell = lambda m, c, t: next((r for r in m["rows"] if r["name"] == c), {"cells": {}})["cells"].get(t)
ONB, STALE, NEWC, CONF = "ocp4-prod-hc08-site1", "ocp4-prod-hc03-site1", "ocp4-prod-hc04-site4", "ocp4-prod-hc05-site1"

# --- model: default view as before; a view with a@dev reflects dev's folders
dev, rel = get("ref=a:dev"), get("ref=c:release")
ok(cell(base, ONB, "a")["status"] == "missing" and cell(dev, ONB, "a")["status"] == "onboarded", "model: a@dev onboards hc08 (missing on main)")
ok(cell(base, STALE, "a")["status"] == "stale" and cell(dev, STALE, "a") is None, "model: a@dev removes the stale folder (removed from the fleet)")
ok(base["conflict_counts"]["conflict"] == 1 and dev["conflict_counts"]["conflict"] == 2, "model: a@dev adds a conflict (cert-sync, same source and revision as b)")
cf = [c for c in dev["conflicts"] if c["cluster"] == CONF and any(e["chart"] == "cert-sync" and e["tenant"] == "a" for e in c["entries"])]
ok(len(cf) == 1 and cf[0]["kind"] == "conflict" and {e["tenant"] for e in cf[0]["entries"]} == {"a", "b"}, "model: the new conflict is on hc05 between a and b")
ok(cell(base, NEWC, "c")["status"] == "missing" and cell(rel, NEWC, "c")["status"] == "onboarded" and cell(rel, ONB, "a")["status"] == "missing", "model: c@release onboards hc04, a stays on main")
both = get("ref=a:dev&ref=c:release")
ok(cell(both, ONB, "a")["status"] == "onboarded" and cell(both, NEWC, "c")["status"] == "onboarded" and (lambda x, y: x["status"] == y["status"] and x["charts"] == y["charts"])(cell(both, "ocp4-prod-hc01-site4", "c"), cell(base, "ocp4-prod-hc01-site4", "c")), "model: two refs combine; untouched cells equal the default view")
ok(cell(get("ref=a:main"), ONB, "a") == cell(base, ONB, "a") and "branches" not in base and "refs" not in base, "model: default branch via the API equals the default view; inventory.json holds only the default view")
ok('"live": true' in open(f"{T}/out/index.html").read() and "dev" not in open(f"{T}/out/INVENTORY.md").read(), "model: page is flagged live; INVENTORY.md is default only")
log = open(f"{T}/serve.log").read()
ok(log.count("downloading archive") == 6 and log.count(f"view a@dev: {dev['refs']['a']['sha'][:7]} downloading archive") == 1 and log.count("view c@release: ") == 3 - 1 and "view a@dev c@release: 1 archive downloads, rendered" not in log, "model: 4 default archives + one per non-default ref, each downloaded once")

rowkey = lambda n, t: json.dumps([n, t], separators=(",", ":"))
with sync_playwright() as p:
    br = p.chromium.launch()

    def page(w=1440, h=900):
        pg = br.new_context(viewport={"width": w, "height": h}).new_page()
        pg.errs = []
        pg.on("console", lambda m: pg.errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.errs.append(str(e)))
        return pg

    def status(pg, n, t):  # the status pill text of one matrix cell
        return pg.locator(f"[data-k='{rowkey(n, t)}']").first.evaluate("b => b.closest('td').querySelector('.pill').innerText.trim()")

    def settle(pg):
        pg.wait_for_function("document.getElementById('main').getAttribute('aria-busy') !== 'true'")

    pg = page(); pg.goto(U + "#tab=clusters"); settle(pg)
    ok(pg.locator("#brbtn").is_visible() and pg.inner_text("#brbtn") == "Branches" and pg.locator("#viewbar").is_hidden() and pg.locator(".btag").count() == 0, "default page: Branches button with no count, no info bar, no tags")
    ok(status(pg, ONB, "a").startswith("Missing"), "default page shows main: hc08 is missing for a")

    # popover: opens, closes, keeps focus, non-modal
    pg.focus("#brbtn"); pg.keyboard.press("Enter")
    ok(pg.locator("#brpop").is_visible() and pg.get_attribute("#brbtn", "aria-expanded") == "true" and pg.evaluate("document.getElementById('brpop').contains(document.activeElement)"), "popover opens from the button (keyboard) and takes focus")
    ok(pg.get_attribute("#brpop", "role") == "dialog" and pg.get_attribute("#brpop", "aria-modal") == "false" and pg.evaluate("!document.getElementById('main').inert && !document.querySelector('.mast').inert"), "popover is non-modal: the page stays interactive")
    rows = pg.locator("#brlist .brrow")
    ok([r.split("\n")[0].strip() for r in rows.all_inner_texts()] == ["a", "b", "c"], "one row per tenant that has more than one branch (a, b, c)")
    ok(pg.locator("#brs0 option").all_inner_texts() == ["main (default)", "dev"] and pg.locator("#brs2 option").all_inner_texts() == ["main (default)", "release"], "branch selects list the default first, marked (default)")
    ok(pg.locator("#brlist .flt").count() == 1 and pg.locator("#brs1 option").count() == 21, "a filter appears only for the tenant with more than 15 branches")
    pg.fill("#brlist .flt", "f1"); n1 = pg.locator("#brs1 option").count()
    ok(n1 == 11 and pg.input_value("#brs1") == "main", f"filter narrows the branches ({n1} options: f10-f19 and the selected main)")
    pg.fill("#brlist .flt", "")
    pg.keyboard.press("Escape")
    ok(pg.locator("#brpop").is_hidden() and pg.evaluate("document.activeElement.id") == "brbtn" and pg.get_attribute("#brbtn", "aria-expanded") == "false", "Esc closes the popover and focus returns to the button")
    pg.click("#brbtn"); pg.click("#tab-conflicts")
    ok(pg.locator("#brpop").is_hidden(), "clicking elsewhere closes it")

    # apply a:dev
    pg.click("#brbtn"); pg.select_option("#brs0", "dev")
    pg.click("#brapply"); settle(pg)
    sha = dev["refs"]["a"]["sha"][:7]
    ok(pg.evaluate("location.hash").startswith("#refs=a:dev") and pg.locator("#brpop").is_hidden() and pg.evaluate("document.activeElement.id") == "brbtn", "Apply: hash holds refs, popover closed, focus back on the button")
    ok(pg.inner_text("#viewbar").replace("\n", " ") == f"Viewing a @ dev ({sha}) · others on default branches · Reset", "info bar: Viewing a @ dev (sha) · others on default branches · Reset")
    ok(pg.locator("#brbtn .bn").inner_text().startswith("1"), "button shows the count of non-default refs")
    pg.click("#tab-clusters")
    ok(status(pg, ONB, "a").startswith("Onboarded"), "data: hc08 is onboarded for a on dev (was Missing)")
    ok(pg.locator("#m thead th .btag").all_inner_texts() == ["dev"] and "a @ dev" in pg.locator("#tenant option").all_inner_texts(), "branch tag in the matrix column header and the switcher")
    pg.select_option("#tenant", "a")
    ok(pg.locator("#ctx-t .btag").inner_text() == "dev" and pg.locator("#ctx-t").inner_text().replace("\n", " ").endswith("a dev"), "branch tag next to the tenant in the title")
    pg.click("#tab-conflicts")
    ok(pg.locator("#conf .tchip .btag").count() >= 1 and set(pg.locator("#conf .tchip .btag").all_inner_texts()) == {"dev"}, "conflict entries: only tenant a carries the dev tag")
    pg.click("#tab-clusters"); pg.locator(f"[data-k='{rowkey(ONB, 'a')}']").click()
    ok(pg.locator("#sheet h3 .btag").inner_text() == "dev" and "Folder" in pg.inner_text("#sheet") and "sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-hc08-site1/" in pg.inner_text("#sheet"), "sheet: tag on the tenant, the path stays repo-relative")
    ok(pg.locator("#sheet button.copy").first.get_attribute("aria-label").startswith("Copy sites/") and "dev" not in pg.locator("#sheet button.copy").first.get_attribute("aria-label"), "copy buttons copy the path only")
    pg.keyboard.press("Escape")
    ok("a@dev" in pg.inner_text("#foot") and not pg.errs, "footer names the ref; no console errors")
    hash_ = pg.evaluate("location.hash")

    # reload restores
    pg2 = page(); pg2.goto(U + hash_); settle(pg2)
    ok(pg2.inner_text("#viewbar").startswith("Viewing a @ dev (" + sha + ")") and pg2.locator("#m thead th .btag").count() >= 1 and not pg2.errs, "reload with the hash restores the view")
    pg2.click("#tab-clusters")
    ok(status(pg2, ONB, "a").startswith("Onboarded") and pg2.input_value("#tenant") == "a", "reload: same data, tenant kept")
    pg2.click("#brbtn")
    ok(pg2.input_value("#brs0") == "dev" and pg2.get_attribute("#brs0", "id") == "brs0", "reload: the popover shows the active branch")
    pg2.keyboard.press("Escape")

    # reset from the info bar
    pg2.click("#vbreset")
    ok("refs" not in pg2.evaluate("location.hash") and pg2.locator("#viewbar").is_hidden() and pg2.locator("#brbtn .bn").count() == 0 and pg2.locator(".btag").count() == 0 and pg2.evaluate("document.activeElement.id") == "brbtn", "Reset: hash, bar, count and tags gone; focus on the button")
    pg2.click("#tab-clusters")
    ok(status(pg2, ONB, "a").startswith("Missing") and pg2.input_value("#tenant") == "a", "Reset: back to main data, tenant kept")
    # reset all to default in the popover
    pg2.click("#brbtn"); pg2.select_option("#brs2", "release"); pg2.click("#brapply"); settle(pg2)
    ok(pg2.inner_text("#viewbar").startswith("Viewing c @ release") and status(pg2, NEWC, "c").startswith("Onboarded"), "apply c:release")
    pg2.click("#brbtn"); pg2.click("#brreset"); settle(pg2)
    ok(pg2.locator("#viewbar").is_hidden() and "refs" not in pg2.evaluate("location.hash") and pg2.locator("#brpop").is_hidden(), "Reset all to default in the popover")

    # a slash in a branch name survives the hash
    pg2.click("#brbtn"); pg2.select_option("#brs1", "feature/f07"); pg2.click("#brapply"); settle(pg2)
    ok("refs=b:feature%2Ff07" in pg2.evaluate("location.hash") and "b @ feature/f07" in pg2.inner_text("#viewbar"), "branch with a slash: encoded in the hash")
    pg2.reload(); settle(pg2)
    ok("b @ feature/f07" in pg2.inner_text("#viewbar") and not pg2.errs, "branch with a slash: restored on reload")

    # two refs, hash order and info bar
    pg3 = page(); pg3.goto(U + "#refs=a:dev,c:release&tenant=a&site=site1&tab=actions"); settle(pg3)
    ok(pg3.inner_text("#viewbar").replace("\n", " ").startswith("Viewing a @ dev (") and ", c @ release (" in pg3.inner_text("#viewbar") and pg3.locator("#brbtn .bn").inner_text().startswith("2"), "two refs: both named in the bar, count 2")
    ok(pg3.evaluate("location.hash") == "#refs=a:dev,c:release&site=site1&tenant=a&tab=actions" and pg3.inner_text("#ctx-t").startswith("site1"), "hash keeps refs, site, tenant and tab")
    ok("Onboarded" in pg3.locator(".tile").first.inner_text() and pg3.locator("#panel-actions").is_visible(), "site/tenant/tab from the hash apply to the branch view")

    # unknown branch in the hash: fall back with a notice naming it
    pg4 = page(); pg4.goto(U + "#refs=a:missing-branch,c:release&tenant=c"); settle(pg4)
    ok("Branch missing-branch of a no longer exists; showing main." in pg4.inner_text("#viewbar") and "c @ release" in pg4.inner_text("#viewbar") and "missing-branch" not in pg4.evaluate("location.hash"), "unknown branch in the hash: default for that tenant, one-line notice naming it, hash cleaned")
    pg4 = page(); pg4.goto(U + "#refs=a:missing-branch"); settle(pg4)
    ok("Branch missing-branch of a no longer exists" in pg4.inner_text("#viewbar") and pg4.locator("#brbtn .bn").count() == 0 and not pg4.errs, "only unknown branches: notice, default view, no count")
    pg4 = page(); pg4.goto(U + "#refs=nope:dev"); settle(pg4)
    ok("Tenant nope was not found." in pg4.inner_text("#viewbar") and not pg4.errs, "unknown tenant in the hash: notice")

    # loading state and errors
    pg5 = page(); pg5.add_init_script("const f = window.fetch; window.fetch = (...a) => new Promise(r => setTimeout(() => r(f(...a)), 1200))"); pg5.goto(U); settle(pg5)
    pg5.click("#brbtn"); pg5.select_option("#brs0", "dev"); pg5.click("#brapply")
    ok(pg5.get_attribute("#brbtn", "aria-busy") == "true" and "busy" in pg5.get_attribute("#brbtn", "class") and pg5.inner_text("#brapply") == "Applying…" and pg5.locator("#brpop").is_visible(), "while loading: busy state on the button, Apply says Applying…")
    settle(pg5)
    pg5.click("#brbtn"); pg5.select_option("#brs2", "release")
    pg5.route("**/api/view*", lambda r: r.fulfill(status=503, headers={"Retry-After": "30", "Content-Type": "application/json"}, body=json.dumps(dict(error="GitLab is rate limiting archive downloads; try again in 30s", retry_after=30))))
    pg5.click("#brapply"); pg5.wait_for_selector("#brerr:not([hidden])")
    ok("try again in 30s" in pg5.inner_text("#brerr") and pg5.get_attribute("#brerr", "role") == "alert" and pg5.locator("#brpop").is_visible() and pg5.get_attribute("#brbtn", "aria-busy") == "false" and "release" not in pg5.inner_text("#viewbar"), "error: inline alert with the server's message, popover stays, view unchanged")
    pg5.unroute("**/api/view*"); pg5.route("**/api/view*", lambda r: r.abort())
    pg5.click("#brapply"); pg5.wait_for_function("document.getElementById('brerr').textContent.includes('reach')")
    ok(pg5.locator("#brerr").is_visible(), "network failure: inline alert")
    ok(not pg5.errs or all("Failed to load resource" in e or "ERR_FAILED" in e for e in pg5.errs), "no console errors beyond the failed requests the test caused")

    # no empty states: only sections with something to act on; one line when nothing is left
    import re
    EMPTY = re.compile(r"\(0\)|Nothing to clean up|Nothing missing|Nothing to add|No conflicts|Nothing to review")
    for hsh in ("#tenant=a&site=site1&tab=actions", "#tenant=c&tab=actions", "#tenant=b&site=site5&tab=actions", "#tenant=a&site=site1&tab=conflicts"):
        pe = page(); pe.goto(U + hsh); settle(pe)
        ok(not EMPTY.search(pe.inner_text("main")), f"no (0) or 'Nothing ...' empty-state text ({hsh})")
    pe = page(); pe.goto(U + "#tenant=a&site=site1&tab=actions"); settle(pe)
    ok(pe.locator("#area h2").all_inner_texts() == ["Missing: create these folders (3)", "Stale: remove these folders (1)", "Available: add these (2)"] and pe.locator(".emptyc").count() == 0, "a at site1: three sections, all above 0")
    pe = page(); pe.goto(U + "#refs=a:dev&tenant=a&site=site1&tab=actions"); settle(pe)
    hs = pe.locator("#area h2").all_inner_texts()
    ok(hs == ["Missing: create these folders (2)", "Available: add these (2)"] and not any("Stale" in x for x in hs), "a@dev at site1: the Stale section (now 0) is not rendered")
    paths = pe.locator("#area .path code, #area .path .p, #area .path").all_inner_texts()
    ok(paths and not any("//" in p for p in paths), "no path has an empty segment (e.g. missing env)")
    pe = page(); pe.goto(U + "#tenant=a&site=_hub&tab=actions"); settle(pe)
    ok(pe.locator("#area h2").count() == 0 and pe.locator("#area").inner_text().strip() == "Nothing to act on for a at Management hub.", "everything empty: the single line, nothing else")
    pe.click("#tab-conflicts"); settle(pe)
    ok("Nothing to act on" in pe.inner_text("#conf") and not EMPTY.search(pe.inner_text("#conf")), "conflicts tab with nothing: the same single line")

    # static mode
    ps = page(); ps.goto(STATIC); ps.wait_for_selector("tr.r")
    ok(ps.locator("#brbtn").is_hidden() and ps.locator("#viewbar").is_hidden() and not ps.errs, "static page (no server): Branches control hidden, no info bar, no errors")
    ps.goto(STATIC + "#refs=a:dev&tenant=random"); ps.reload()
    ok(ps.locator("#brbtn").is_hidden() and "need the running server" in ps.inner_text("#viewbar") and not ps.errs and ps.locator(".btag").count() == 0, "static page with refs in the hash: control hidden, one-line notice, default data")

    # mobile
    pm = page(390, 844); pm.goto(U + "#refs=a:dev&tenant=a&site=site1&tab=actions"); settle(pm)
    ok(pm.evaluate("document.documentElement.scrollWidth") <= 390 and pm.locator("#viewbar").is_visible() and pm.locator("#brbtn").is_visible(), "390px: no horizontal scroll with the info bar and button")
    pm.click("#brbtn")
    bb = pm.locator("#brpop").bounding_box()
    ok(pm.evaluate("document.documentElement.scrollWidth") <= 390 and bb["x"] >= 0 and bb["x"] + bb["width"] <= 390, f"390px: popover fits the screen ({bb['x']:.0f}..{bb['x'] + bb['width']:.0f}), no horizontal scroll")
    pm.keyboard.press("Escape")
    pm.goto(U + "#tab=clusters"); settle(pm)
    ok(pm.evaluate("document.documentElement.scrollWidth") <= 390 and not pm.errs, "390px: default page, no horizontal scroll, no errors")
    br.close()

for pr in procs:
    pr.terminate()
print("branch_ui_test: %d FAILED" % len(fails) if fails else "branch_ui_test: all passed")
sys.exit(1 if fails else 0)
