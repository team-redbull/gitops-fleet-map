#!/usr/bin/env python3
"""Conflict + available-charts fixtures. Usage: conflict_fixtures.py DIR -> DIR/sigs, DIR/day1.
h1 same folder name, other repos: none | h2 same source+revision, two tenants: conflict | h3 same, one tenant, two folders: conflict
h4 same source, other revision: version-skew | h5 two at main + one at v1.4: conflict and skew | h6 unset vs main: skew (HEAD != main)
h7 unset on both: conflict at HEAD | h8 .git/case/slash/./ spellings: conflict | h9 same repo, other path: none
h10 empty repourl: none | h11 available charts: catalog - deployed - excluded"""
import os, sys

root = sys.argv[1]
MCE = "ocp4-prod-mce-site1-a"
H = lambda n: f"ocp4-prod-h{n}-site1"


def mk(*p, text=None):
    path = os.path.join(root, *p)
    if text is None:
        os.makedirs(path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)


def cfg(repo="", path="", rev="", extra=""):
    return "".join(f"{k}: {v}\n" for k, v in (("repourl", repo), ("path", path), ("targetRevision", rev)) if v) + extra


mk("day1", "sites", "site1", "mces", MCE, "version.yaml", text="mastertag: 4.16.30-x86_64\n")
for n in range(1, 12):
    mk("day1", "sites", "site1", "mces", MCE, "hostedClusters", H(n) + ".yaml", text="mastertag: 4.16.30-x86_64\n")
for t in ("alpha", "beta"):
    mk("sigs", t, "sites", "site1", "prod", "mces", MCE)


def chart(tenant, n, name, **kw):
    mk("sigs", tenant, "operators", name, name + ".yaml", text=cfg(**kw))
    mk("sigs", tenant, "sites", "site1", "prod", "mces", MCE, H(n), name)


G = lambda x: f"https://git.example/{x}.git"
chart("alpha", 1, "shared", repo=G("a"), rev="main"); chart("beta", 1, "shared", repo=G("b"), rev="main")
chart("alpha", 2, "c2a", repo=G("c2"), rev="main"); chart("beta", 2, "c2b", repo=G("c2"), rev="main")
chart("alpha", 3, "one", repo=G("c3"), rev="main"); chart("alpha", 3, "two", repo=G("c3"), rev="main")
chart("alpha", 4, "s4a", repo=G("c4"), rev="main"); chart("beta", 4, "s4b", repo=G("c4"), rev="v1.4")
chart("alpha", 5, "m5a", repo=G("c5"), rev="main"); chart("beta", 5, "m5b", repo=G("c5"), rev="main"); chart("alpha", 5, "m5c", repo=G("c5"), rev="v1.4")
chart("alpha", 6, "u6a", repo=G("c6")); chart("beta", 6, "u6b", repo=G("c6"), rev="main")
chart("alpha", 7, "u7a", repo=G("c7")); chart("beta", 7, "u7b", repo=G("c7"))
chart("alpha", 8, "n8a", repo="https://Git.example/Org/Chart.git", path="./charts/x/", rev="main"); chart("beta", 8, "n8b", repo="https://git.example/org/chart/", path="charts/x", rev="main")
chart("alpha", 9, "p9x", repo=G("c9"), path="charts/x", rev="main"); chart("beta", 9, "p9y", repo=G("c9"), path="charts/y", rev="main")
chart("alpha", 10, "e1", rev="main"); chart("alpha", 10, "e2", rev="main")
# h11: alpha catalog = everything above + own1, dflt, excl, extra, lonely; deployed own1 (own) + dflt (fleet default); excl is a default excluded here
for c in ("own1", "dflt", "excl", "extra", "lonely"):
    mk("sigs", "alpha", "operators", c, c + ".yaml", text=cfg(repo=G(c), rev="v1" if c == "extra" else ""))
mk("sigs", "alpha", "operators", "extra", "ocp-versions", "4.16.30", "extra.yaml", text="targetRevision: v1-pin\n")
mk("sigs", "alpha", "operators", "lonely", "ocp-versions", "4.15.1", "lonely.yaml", text="targetRevision: other\n")
mk("sigs", "alpha", "sites", "site1", "prod", "mces", MCE, H(11), "own1")
for c in ("dflt", "excl"):
    mk("sigs", "alpha", "defaults", "hosted-clusters", c)
mk("sigs", "alpha", "defaults", "hosted-clusters", "exclusions.yaml", text=f"exclusions:\n  excl:\n    - {H(11)}\n")
# beta has no folder on h11: its own catalog is offered
mk("sigs", "beta", "operators", "bchart", "bchart.yaml", text=cfg(repo=G("bchart")))
