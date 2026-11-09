#!/usr/bin/env python3
"""Provenance fixture set: version pins, layered values, exclusions, repoUrl casing, parse errors, two sites, two tenants.
Usage: prov_fixtures.py DIR  -> DIR/sigs, DIR/day1."""
import os, sys

root = sys.argv[1]
MA, MC = "ocp4-prod-mce-site1-a", "ocp4-prod-mce-site4-a"
A, B, C = "ocp4-prod-a-site1", "ocp4-prod-b-site1", "ocp4-prod-c-site4"


def mk(*p, text=None):
    path = os.path.join(root, *p)
    if text is None:
        os.makedirs(path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)


CFG = "repourl: https://git.example/%s.git\ntargetRevision: main\npath: charts/%s\nprojectNamespace: %s-ns\n"
cfg = lambda c, extra="": CFG % (c, c, c) + extra
tag = lambda v: f"mastertag: {v}-x86_64\n"

# day1: A is on 4.16.27 (pinned), B on 4.16.30 (no pin layer), C on 4.16.30
for site, m, hs in (("site1", MA, [(A, "4.16.27"), (B, "4.16.30")]), ("site4", MC, [(C, "4.16.30")])):
    mk("day1", "sites", site, "mces", m, "version.yaml", text=tag("4.16.27"))
    for h, v in hs:
        mk("day1", "sites", site, "mces", m, "hostedClusters", h + ".yaml", text=tag(v))

t = ["sigs", "acme"]
# --- chart `pinned` (own on A, B, C): team default + a 4.16.27 pin, so B (4.16.30) has a missing layer
mk(*t, "operators", "pinned", "pinned.yaml", text=cfg("pinned", "syncPolicy:\n  automated:\n    prune: true\n"))
mk(*t, "operators", "pinned", "ocp-versions", "4.16.27", "pinned.yaml", text="targetRevision: release-4.16.27\n")
mk(*t, "operators", "pinned", "values.yaml", text="a: team\nlist: [1, 2, 3]\nm:\n  x: 1\n  y: 1\nonlyteam: yes\n")
# --- chart `overr` (fleet default for hosted clusters): defaults sets targetRevision above the pin
mk(*t, "operators", "overr", "overr.yaml", text=cfg("overr"))
mk(*t, "operators", "overr", "ocp-versions", "4.16.27", "overr.yaml", text="targetRevision: release-4.16.27\n")
mk(*t, "defaults", "hosted-clusters", "overr", "overr.yaml", text="targetRevision: fleet-main\n")
# --- chart `vals` (fleet default): every values layer sets something
mk(*t, "operators", "vals", "vals.yaml", text=cfg("vals"))
mk(*t, "operators", "vals", "values.yaml", text="a: team\nlist: [1, 2, 3]\nm:\n  x: 1\n  y: 1\ndb:\n  password: team-pw\n  host: db\n" + "".join(f"k{i}: {i}\n" for i in range(14)))
mk(*t, "sites", "site1", "values.yaml", text="a: site\nsiteonly: 1\n")
mk(*t, "sites", "site4", "values.yaml", text="registry: r5\n")
mk(*t, "sites", "site1", "prod", "values.yaml", text="m:\n  x: 2\n")
mk(*t, "defaults", "hosted-clusters", "vals", "vals.yaml", text="syncPolicy:\n  automated: {}\n")
mk(*t, "defaults", "hosted-clusters", "vals", "values-prod.yaml", text="m:\n  y: 3\n")
mk(*t, "defaults", "hosted-clusters", "vals", f"values-{A}.yaml", text="list: [9]\n")
# --- exclusion: C does not get `overr`
mk(*t, "defaults", "hosted-clusters", "exclusions.yaml", text=f"exclusions:\n  overr:\n    - {C}\n")
# --- repoUrl casing + malformed values
mk(*t, "operators", "kyv", "kyv.yaml", text="repoUrl: https://git.example/kyv.git\ntargetRevision: main\n")
mk(*t, "operators", "broken", "broken.yaml", text=cfg("broken"))
mk(*t, "operators", "broken", "values.yaml", text="{{{ nope\n")
# --- cluster folders
site = t + ["sites", "site1", "prod", "mces"]
mk(*site, MA, "in-cluster", "pinned")
for h in (A, B):
    mk(*site, MA, h, "pinned")
mk(*site, MA, A, "kyv"); mk(*site, MA, A, "broken")
mk(*site, MA, A, "values.yaml", text="a: cluster\ndb:\n  password: hunter2\n")      # cluster-wide
mk(*site, MA, A, "pinned", "values.yaml", text="a: own\n")                          # this cluster's chart
mk(*t, "sites", "site4", "prod", "mces", MC, C, "pinned")
# UPI cluster with its own version, hub chart + hub-wide values
mk(*t, "sites", "site1", "prod", "upi", "ocp4-upi01-site1", "pinned")
mk(*t, "sites", "site1", "prod", "upi", "ocp4-upi01-site1", "version.yaml", text=tag("4.14.40"))
mk(*t, "sites", "site1", "prod", "upi", "values.yaml", text="upi: true\n")
mk(*t, "defaults", "hub", "hubchart", "hubchart.yaml", text=cfg("hubchart"))
mk(*t, "defaults", "hub", "values.yaml", text="siteNetworks: [a, b]\n")
mk(*t, "operators", "hubchart", "values.yaml", text="x: 1\n")

# second tenant: shares cluster B so the sheet has two sections
mk("sigs", "beta", "operators", "pinned", "pinned.yaml", text=cfg("pinned"))
mk("sigs", "beta", "sites", "site1", "prod", "mces", MA, B, "pinned")
