#!/usr/bin/env python3
"""Fixtures for 'in day1, not in day2'. Usage: nid_fixtures.py DIR -> DIR/sigs (ta, tb), DIR/day1.
site1: ok (ta has it), hc03 (expected ta,tb; no folder), hc09 (expected nobody), moved (only a stale env-mismatch folder: NOT listed), lab (no env in name).
site2: an MCE no tenant has, plus two hosted clusters on it (one expected for tb, one for nobody)."""
import os, sys

root = sys.argv[1]
M1, M2 = "ocp4-prod-mce-site1-a", "ocp4-prod-mce-site2-a"


def mk(*p, text=""):
    path = os.path.join(root, *p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


tag = "mastertag: 4.16.30-x86_64\n"
d = ["day1", "sites"]
mk(*d, "configValues.yaml", text="day2:\n  copy_to_sigs: [ta, tb]\n")
for site, m, vals in (("site1", M1, "bar: 1\n"), ("site2", M2, "day2:\n  copy_to_sigs: []\n")):
    mk(*d, site, "mces", m, "version.yaml", text=tag)
    mk(*d, site, "mces", m, "values.yaml", text=vals)
hc = lambda site, m, n, text=tag: mk(*d, site, "mces", m, "hostedClusters", n + ".yaml", text=text)
for n in ("ok", "hc03", "moved"):
    hc("site1", M1, f"ocp4-prod-{n}-site1")
hc("site1", M1, "ocp4-prod-hc09-site1", tag + "day2:\n  copy_to_sigs: []\n")
hc("site1", M1, "ocp4-lab-env-site1")
hc("site2", M2, "ocp4-prod-h1-site2")
hc("site2", M2, "ocp4-prod-h2-site2", tag + "day2:\n  copy_to_sigs: [tb]\n")

s = lambda t, *p: mk("sigs", t, "sites", "site1", *p, "c1", "c1.yaml")
s("ta", "prod", "mces", M1, "in-cluster")
s("ta", "prod", "mces", M1, "ocp4-prod-ok-site1")
s("tb", "prod", "mces", M1, "in-cluster")
s("tb", "prep", "mces", M1, "ocp4-prod-moved-site1")   # env differs from day1: stale, still 'has a folder'
