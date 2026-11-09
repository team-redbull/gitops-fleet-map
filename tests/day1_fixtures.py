#!/usr/bin/env python3
"""day1-driven fixtures: copy_to_sigs layers (list replace), .yml cluster, non-convention names, upgrade in progress.
Usage: day1_fixtures.py DIR -> DIR/sigs (ta, tb, tc), DIR/day1."""
import os, sys

root = sys.argv[1]
MP, MQ, MW = "ocp4-prod-mce-site1-a", "ocp4-prep-mce-site1-a", "ocp4-mce-lab"


def mk(*p, text=""):
    path = os.path.join(root, *p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


tag = lambda v: f"mastertag: {v}-x86_64\n"
d = ["day1", "sites"]
mk(*d, "configValues.yaml", text="day2:\n  copy_to_sigs: [ta, tb]\n")
mk(*d, "site1", "values.yaml", text="foo: 1\n")
for m, v in ((MP, "4.16.30"), (MQ, "4.16.30"), (MW, "4.20.35")):
    mk(*d, "site1", "mces", m, "version.yaml", text=tag(v))
    mk(*d, "site1", "mces", m, "values.yaml", text={MQ: "day2:\n  copy_to_sigs: [tc]\n", MW: "day2:\n  copy_to_sigs: []\n"}.get(m, "bar: 1\n"))
hc = lambda m, f, text: mk(*d, "site1", "mces", m, "hostedClusters", f, text=text)
hc(MP, "ocp4-prod-x1-site1.yaml", tag("4.16.30"))                                    # expected ta, tb
hc(MP, "ocp4-prod-x2-site1.yaml", tag("4.16.30") + "day2:\n  copy_to_sigs: []\n")    # cluster file narrows to nobody
hc(MP, "ocp4-prod-up-site1.yaml", tag("4.16.30") + "nodepool:\n  - {name: a, workertag: 4.16.29-x86_64}\n  - {name: b, workertag: 4.16.30-x86_64}\n")
hc(MQ, "ocp4-prep-y1-site1.yml", tag("4.16.30"))                                     # .yml, expected tc via the MCE layer
hc(MW, "ocp4-upgrade-demo.yaml", tag("4.20.35"))                                     # no env token in the name

s = lambda t, *p: mk("sigs", t, "sites", "site1", *p, "c1", "c1.yaml")
s("ta", "prod", "mces", MP, "in-cluster")
s("ta", "prod", "mces", MP, "ocp4-prod-x1-site1")                                    # expected + folder: onboarded, registered by day1
s("ta", "prod", "mces", MP, "ocp4-prod-x2-site1")                                    # folder, not expected: onboarded, manual
s("ta", "prod", "mces", MW, "in-cluster")                                            # env-less MCE: must not be stale
s("ta", "prod", "mces", MW, "ocp4-upgrade-demo")                                     # env-less cluster: must not be stale
s("tb", "prod", "mces", MP, "in-cluster")
s("tc", "prep", "mces", MQ, "in-cluster")
s("tc", "prep", "mces", MQ, "ocp4-prep-y1-site1")                                    # .yml cluster onboarded by tc
