#!/usr/bin/env python3
"""Screenshots of the branch picker against a running instance. Usage: branch_shots.py URL  (e.g. http://127.0.0.1:18095/) -> tests/shots/v7-*.png"""
import os, sys
from playwright.sync_api import sync_playwright

U, OUT = sys.argv[1], os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
H = "#refs=a:dev&tenant=a&site=site1&tab=actions"
with sync_playwright() as p:
    br = p.chromium.launch()
    page = lambda w, h, scheme="light": br.new_context(viewport={"width": w, "height": h}, color_scheme=scheme).new_page()
    settle = lambda pg: pg.wait_for_function("document.getElementById('main').getAttribute('aria-busy') !== 'true'")
    pg = page(1440, 900); pg.goto(U + "#tenant=a&site=site1&tab=actions"); settle(pg)
    pg.click("#brbtn"); pg.select_option("#brs0", "dev"); pg.select_option("#brs1", "release"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/v7-branches-popover.png")
    pg = page(1440, 900); pg.goto(U + H); settle(pg); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/v7-viewing-dev.png")
    pg = page(1440, 900, "dark"); pg.goto(U + H); settle(pg); pg.click("#brbtn"); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/v7-viewing-dev-dark.png")
    pg = page(390, 844); pg.goto(U + H); settle(pg); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/v7-viewing-dev-mobile.png")
    pg.click("#brbtn"); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/v7-branches-popover-mobile.png")
    br.close()
