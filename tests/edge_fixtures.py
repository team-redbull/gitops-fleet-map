#!/usr/bin/env python3
"""Builds the edge-case fixture set (hostile names, malformed YAML, duplicate folders, env mismatch).
Usage: edge_fixtures.py DIR  -> DIR/sigs, DIR/day1. Generated, not committed: some names are awkward on Windows."""
import os, sys

root = sys.argv[1]
MCE_A, MCE_B, MCE_P = "ocp4-prod-mce-site1-a", "ocp4-prod-mce-site1-b", "ocp4-prep-mce-site1-a"
XSS_TENANT = "<img src=x onerror=alert(1)>"
XSS_SITE = "<img src=x onerror=alert(3)>"
XSS_CLUSTER = "\"quote's <img src=x onerror=alert(2)>"


def mk(*p, text=None):
    path = os.path.join(root, *p)
    if text is None:
        os.makedirs(path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)


# day1: three MCEs, a few hosted clusters
for m in (MCE_A, MCE_B, MCE_P):
    mk("day1", "sites", "site1", "mces", m, "version.yaml", text="mastertag: 4.16.30-x86_64\n")
for m, h in ((MCE_A, "ocp4-prod-dup-site1"), (MCE_A, "ocp4-prod-both-site1"), (MCE_P, "ocp4-prep-envy-site1")):
    mk("day1", "sites", "site1", "mces", m, "hostedClusters", h + ".yaml", text="mastertag: 4.16.30-x86_64\n")

# alpha: malformed files + duplicate folders + hub chart
a = ["sigs", "alpha"]
mk(*a, "defaults", "mces", "chart-a")
mk(*a, "defaults", "mces", "exclusions.yaml", text="- just\n- a list\n")          # list, not a mapping
mk(*a, "defaults", "hosted-clusters", "logging")
mk(*a, "defaults", "hosted-clusters", "exclusions.yaml", text="exclusions:\n  logging: not-a-list\n  other: [x]\n")
mk(*a, "defaults", "upi", "exclusions.yaml", text="just a string\n")
mk(*a, "defaults", "hub", "hubchart")
site = a + ["sites", "site1", "prod"]
mk(*site, "mces", MCE_A, "in-cluster", "c1")
mk(*site, "mces", MCE_B, "in-cluster", "c1")
mk(*site, "mces", MCE_A, "ocp4-prod-dup-site1", "c1")                              # same hosted cluster
mk(*site, "mces", MCE_B, "ocp4-prod-dup-site1", "c1")                              # under two MCEs
mk(*site, "mces", MCE_A, "ocp4-prod-both-site1", "c1")                             # hosted ...
mk(*site, "upi", "ocp4-prod-both-site1", "c1")                                     # ... and UPI
mk(*site, "upi", "ocp4-bad-version-site1", "c1")
mk(*site, "upi", "ocp4-bad-version-site1", "version.yaml", text="- a\n- b\n")      # list, not a mapping
mk(*site, "upi", "ocp4-broken-yaml-site1", "c1")
mk(*site, "upi", "ocp4-broken-yaml-site1", "version.yaml", text="{{{ nope\n")

# beta: env mismatch (day1 says prep, folder sits under prod)
mk("sigs", "beta", "sites", "site1", "prod", "mces", MCE_P, "ocp4-prep-envy-site1", "c1")

# hostile names: tenant and cluster folder
mk("sigs", XSS_TENANT, "sites", "site1", "prod", "mces", MCE_A, XSS_CLUSTER, "c1")
mk("sigs", "quote's \"tenant\"", "sites", "site1", "prod", "mces", MCE_A, "in-cluster", "c1")
mk("sigs", XSS_TENANT, "sites", XSS_SITE, "prod", "mces", "ocp4-prod-mce-hostile-a", "in-cluster", "c1")                   # hostile site name
