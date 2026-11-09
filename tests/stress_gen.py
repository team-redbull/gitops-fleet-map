#!/usr/bin/env python3
"""Screenshot-only stress data (NOT a test fixture).
Usage: stress_gen.py OUTDIR  -> writes OUTDIR/{sigs,day1} trees, then OUTDIR/out (with day1) and OUTDIR/out-noday1."""
import os, random, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
root = sys.argv[1]
random.seed(7)
TENANTS = ["team-x", "random", "payments", "telemetry", "edge-ops", "data-platform"]
MCES = [("site1", "prod", "ocp4-prod-mce-site1-a"), ("site1", "prep", "ocp4-prep-mce-site1-a"),
        ("site2", "prod", "ocp4-prod-mce-site2-a"), ("site3", "prod", "ocp4-prod-mce-site3-a"),
        ("site4", "prod", "ocp4-prod-mce-site4-a"), ("site5", "prod", "ocp4-prod-mce-site5-a"), ("site5", "prep", "ocp4-prep-mce-site5-a")]
UPI = [("site1", "prod", "ocp4-upi01-site1"), ("site2", "prod", "ocp4-legacy-site2"),
       ("site3", "prod", "ocp4-lab-site3"), ("site1", "prep", "ocp4-sandbox-site1"), ("site4", "prod", "ocp4-edge-site4")]
CHARTS = ["cluster-roles", "kyverno", "logging", "bmhgen", "monitoring-rules", "network-policies"]
hosted = {m: [f"ocp4-{m[1]}-{w}-{m[0]}" for w in random.sample(
    [f"hc{i:02d}" for i in range(1, 26)], 13)] for m in MCES}


def touch(*p):
    os.makedirs(os.path.join(root, *p[:-1]), exist_ok=True)
    open(os.path.join(root, *p), "w").write(p[-1] if p[-1].endswith(".yaml") else "")


def mk(*p):
    os.makedirs(os.path.join(root, *p), exist_ok=True)


# day1: every hosted cluster except 3 that only exist in tenant repos (stale)
ghosts = {random.choice(hosted[m]) for m in MCES[:3]}
for s, e, m in MCES:
    d = ["day1", "sites", s, "mces", m]
    os.makedirs(os.path.join(root, *d, "hostedClusters"), exist_ok=True)
    open(os.path.join(root, *d, "version.yaml"), "w").write("mastertag: 4.16.%d-x86_64\n" % random.choice([27, 30, 33]))
    for h in hosted[(s, e, m)]:
        if h not in ghosts:
            open(os.path.join(root, *d, "hostedClusters", h + ".yaml"), "w").write("mastertag: 4.16.30-x86_64\n")

for i, t in enumerate(TENANTS):
    base = ["sigs", t]
    if i == 1:
        mk(*base, "defaults", "hosted-clusters", "logging")
    if i == 0:
        mk(*base, "defaults", "mces", "example-token")
    if t == "team-x":
        mk(*base, "defaults", "hub", "example-chart")
    for (s, e, m) in MCES:
        if (i + len(m)) % 5 == 4:  # tenant skips this MCE entirely
            continue
        mk(*base, "sites", s, e, "mces", m, "in-cluster", "kyverno" if i < 2 else "ops-" + t)
        for h in hosted[(s, e, m)]:
            if random.random() < 0.45:
                ch = ["app-" + t, random.choice(CHARTS[:3])] if random.random() < .08 else ["app-" + t]
                for c in ch:
                    mk(*base, "sites", s, e, "mces", m, h, c)
    for (s, e, u) in UPI:
        if random.random() < .5:
            mk(*base, "sites", s, e, "upi", u, "cluster-roles")
            open(os.path.join(root, *base, "sites", s, e, "upi", u, "version.yaml"), "w").write("mastertag: 4.14.40-x86_64\n")
    # force one conflict-heavy cluster and make sure ghosts are stale for someone
    if i < 3:
        g = sorted(ghosts)[i % len(ghosts)]
        s, e, m = next(x for x in MCES if g in hosted[x])
        mk(*base, "sites", s, e, "mces", m, g, "cluster-roles")

# provenance content: deploy configs, values layers (site, site/env, fleet defaults), version pins
def wr(*p):
    path = os.path.join(root, *p[:-1])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(p[-1])


for i, t in enumerate(TENANTS):
    b = ["sigs", t]
    for c in CHARTS + ["app-" + t, "ops-" + t, "example-token"]:
        wr(*b, "operators", c, c + ".yaml", "repourl: https://git.example/%s.git\ntargetRevision: main\npath: charts/%s\nprojectNamespace: %s\nsyncPolicy:\n  automated:\n    prune: %s\n" % (c, c, c, "true" if i % 2 else "false"))
        wr(*b, "operators", c, "values.yaml", "replicas: 2\nimage:\n  tag: v1\n  pullSecret: abc\nresources:\n  limits: {cpu: 500m}\n")
    for c in ("kyverno", "logging"):  # pins for 4.16.30 only: clusters on 4.16.27 / 4.16.33 fall back (missing layer)
        wr(*b, "operators", c, "ocp-versions", "4.16.30", c + ".yaml", "targetRevision: release-4.16.30\n")
        wr(*b, "operators", c, "ocp-versions", "4.16.30", "values.yaml", "replicas: 3\n")
    if i == 1:  # fleet default above the pin: override warning
        wr(*b, "defaults", "hosted-clusters", "logging", "logging.yaml", "targetRevision: fleet-main\nrepoUrl: https://git.example/logging.git\n")
        wr(*b, "defaults", "hosted-clusters", "logging", "values-prod.yaml", "replicas: 5\nresources:\n  limits: {memory: 1Gi}\n")
    if i < 3:
        for sname in {x[0] for x in MCES}:
            wr(*b, "sites", sname, "values.yaml", "registry: registry.%s.example\nproxy:\n  url: http://proxy.%s:3128\n  token: s3cret\n" % (sname, sname))
        for sname, e in {x[:2] for x in MCES}:
            wr(*b, "sites", sname, e, "values.yaml", "dns:\n  zone: %s.%s.example\nimage:\n  tag: v2\n" % (e, sname))
    if t == "team-x":
        wr(*b, "defaults", "hub", "example-chart", "example-chart.yaml", "repourl: https://git.example/hub.git\ntargetRevision: main\n")

for out, extra in (("out", ["--day1-dir", os.path.join(root, "day1")]), ("out-noday1", [])):
    subprocess.run([sys.executable, os.path.join(HERE, "..", "fleetmap.py"), "generate", "--out", os.path.join(root, out),
                    "--sigs-dir", os.path.join(root, "sigs")] + extra, check=True)
