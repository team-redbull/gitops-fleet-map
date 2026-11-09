#!/usr/bin/env python3
"""Screenshots of the stress set. Usage: shots.py STRESS_DIR (from stress_gen.py) [PROV_OUT CONFLICT_OUT] -> tests/shots/v4-*.png, v5-*.png (ladders, conflicts, available charts)"""
import json, os, sys
from playwright.sync_api import sync_playwright

d = sys.argv[1]
U = f"file://{d}/out/index.html"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
m = json.load(open(f"{d}/out/inventory.json"))
# a hosted cluster whose 'random' cell has a chart with a warning and a values stack with overrides
T = "random"
pick = next((r for r in m["rows"] if r["kind"] == "hosted" and any(p["w"] for p in r["cells"][T].get("pv", {}).values()) and "logging" in r["cells"][T].get("pv", {})), None)
name = pick["name"]; key = json.dumps([name, T], separators=(",", ":"))
shot = lambda pg, f: pg.screenshot(path=f"{OUT}/v4-{f}.png")
with sync_playwright() as p:
    br = p.chromium.launch()
    def page(w=1440, h=900, scheme="light"):
        return br.new_context(viewport={"width": w, "height": h}, color_scheme=scheme).new_page()
    pg = page(); pg.goto(U + f"#tenant={T}&tab=clusters&fold=site4"); pg.wait_for_timeout(300); shot(pg, "clusters-folded")
    pg.locator("button.shs[data-site=site1]").click(); pg.wait_for_timeout(400); shot(pg, "site-sheet")
    for scheme, w, h, f in (("light", 1440, 2300, "cell-sheet"), ("dark", 1440, 2300, "cell-sheet-dark"), ("light", 390, 844, "mobile-sheet")):
        pg = page(w, h, scheme); pg.goto(U + f"#tenant={T}&tab=clusters"); pg.locator(f"[data-k='{key}']").first.click(); pg.wait_for_timeout(400)
        rows = pg.locator("#sheet li:has(> button.chh)")
        for i in range(rows.count()):
            if rows.nth(i).locator(".tg.warn").count():
                rows.nth(i).locator("button.chh").click(); break
        pg.wait_for_timeout(250); shot(pg, f)
        if f == "mobile-sheet":
            pg.locator("#sbody").evaluate("e => e.scrollTop = 520"); pg.wait_for_timeout(150); shot(pg, "mobile-sheet-scrolled")
    nshot = lambda pg, f: pg.screenshot(path=f"{OUT}/nav-{f}.png")
    pg = page(); pg.goto(U + "#tab=clusters"); pg.wait_for_timeout(300); nshot(pg, "all-sites")
    pg = page(); pg.goto(U + f"#site=site2&tenant={T}"); pg.wait_for_timeout(300); nshot(pg, "site-actions")
    pg = page(); pg.goto(U + f"#site=site2&tenant={T}&tab=clusters"); pg.wait_for_timeout(300); nshot(pg, "site-clusters")
    pg = page(); pg.goto(U + "#site=site1&tab=settings"); pg.wait_for_timeout(300); nshot(pg, "site-settings")
    pg = page(scheme="dark"); pg.goto(U + f"#site=site2&tenant={T}"); pg.wait_for_timeout(300); nshot(pg, "site-actions-dark")
    pg = page(390, 844); pg.goto(U + f"#site=site2&tenant={T}"); pg.wait_for_timeout(300); nshot(pg, "mobile")
    if len(sys.argv) > 3:  # v5: only what exists, two conflict kinds, available charts (generated outputs of prov_fixtures / conflict_fixtures)
        PU, CU = (f"file://{d}/index.html" for d in sys.argv[2:4])
        s5 = lambda pg, f: pg.screenshot(path=f"{OUT}/v5-{f}.png")
        pg = page(1440, 1500); pg.goto(PU + "#site=site1&tab=settings"); pg.wait_for_timeout(300); s5(pg, "site-settings")
        pg = page(1440, 1500); pg.goto(CU + "#site=site1&tab=settings"); pg.wait_for_timeout(300); s5(pg, "site-settings-empty")
        for scheme in ("light", "dark"):
            pg = page(1440, 2000, scheme); pg.goto(PU + "#tab=clusters"); pg.locator("[data-k='[\"ocp4-prod-a-site1\",\"acme\"]']").click(); pg.wait_for_timeout(350)
            pg.locator("#sheet li:has(> button.chh .cn:text-is('vals')) button.chh").click(); pg.wait_for_timeout(200); s5(pg, "ladders-" + scheme)
            pg = page(1440, 1500, scheme); pg.goto(CU + "#tenant=alpha&tab=clusters"); pg.locator("[data-k='[\"ocp4-prod-h11-site1\",\"alpha\"]']").click(); pg.wait_for_timeout(350); s5(pg, "available-" + scheme)
            pg = page(1440, 1100, scheme); pg.goto(CU + "#tab=conflicts"); pg.wait_for_timeout(300); s5(pg, "conflicts-" + scheme)
        pg = page(1440, 1100); pg.goto(CU + "#tenant=alpha&tab=clusters"); pg.locator("[data-k='[\"ocp4-prod-h4-site1\",\"alpha\"]']").click(); pg.wait_for_timeout(350); s5(pg, "cell-skew")
        pg = page(390, 900); pg.goto(CU + "#tab=conflicts"); pg.wait_for_timeout(300); s5(pg, "conflicts-mobile")
        pg = page(1440, 900); pg.goto(CU); pg.wait_for_timeout(300); s5(pg, "tiles")
    br.close()
print("cluster:", name)
