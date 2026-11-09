#!/usr/bin/env python3
"""Seeds GitLab (real or fake) with made-up teams a/b/c and a made-up day1 repo, all through the REST API.
Usage: seed.py URL ROOT_TOKEN
Layout of the story (what each tenant is meant to show) is in the comments below; check.sh asserts it."""
import argparse, base64, json, urllib.error, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("url"); ap.add_argument("token")
A = ap.parse_args()
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, path, body=None, nf=False):
    req = urllib.request.Request(f"{A.url}/api/v4/{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"PRIVATE-TOKEN": A.token, "Content-Type": "application/json"})
    try:
        with OPEN.open(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 409 or (nf and e.code == 404):
            return None
        raise SystemExit(f"{method} {path}: {e.code} {e.read().decode()[:300]}")


def group(path, parent=None, full=None):
    g = api("POST", "groups", dict(name=path, path=path, **({"parent_id": parent} if parent else {})))
    return g["id"] if g else api("GET", "groups/" + (full or path).replace("/", "%2F"))["id"]


def project(gid, path, full):
    p = api("POST", "projects", dict(name=path, path=path, namespace_id=gid))
    return full.replace("/", "%2F")


def push(proj, files, msg):
    if api("GET", f"projects/{proj}/repository/tree?ref=main"):
        print(f"  {proj}: already has content, skipped")
        return
    acts = [dict(action="create", file_path=p, content=base64.b64encode(c if isinstance(c, bytes) else c.encode()).decode(), encoding="base64") for p, c in sorted(files.items())]
    api("POST", f"projects/{proj}/repository/commits", dict(branch="main", commit_message=msg, actions=acts))
    print(f"  {proj}: {len(files)} files")


def branch(proj, name, files, delete, msg):
    """Creates branch `name` from main with one commit (idempotent: an existing branch is left alone)."""
    if api("GET", f"projects/{proj}/repository/branches/{name}", nf=True):
        print(f"  {proj}@{name}: already there, skipped")
        return
    acts = [dict(action="create", file_path=p, content=c) for p, c in sorted(files.items())] + [dict(action="delete", file_path=p) for p in delete]
    api("POST", f"projects/{proj}/repository/commits", dict(branch=name, start_branch="main", commit_message=msg, actions=acts))
    print(f"  {proj}@{name}: +{len(files)} -{len(delete)}")


tag = lambda v: f"mastertag: {v}-x86_64\n"

# ---------------- day1: generated, made-up (site1 with a lab MCE, site4, site5) ----------------
MP, MQ, MW, MF, MN = "ocp4-prod-mce-site1-a", "ocp4-prep-mce-site1-a", "ocp4-mce-lab", "ocp4-prod-mce-site4-a", "ocp4-prod-mce-site5-a"
day1 = {"sites/configValues.yaml": "day2:\n  copy_to_sigs: [a, b]\n"}
for site, m, v, extra in (("site1", MP, "4.16.27", ""), ("site1", MQ, "4.20.9", ""), ("site1", MW, "4.20.35", "day2:\n  copy_to_sigs: []\n"),  # MW: name has no env, never registered
                          ("site4", MF, "4.16.27", "day2:\n  copy_to_sigs: [c]\n"), ("site5", MN, "4.16.27", "")):  # site4: narrowed to c only
    day1[f"sites/{site}/values.yaml"] = f"# {site}\n"
    day1[f"sites/{site}/mces/{m}/version.yaml"] = tag(v)
    day1[f"sites/{site}/mces/{m}/values.yaml"] = tag(v) + extra
hc = lambda site, m, name, body, ext="yaml": day1.__setitem__(f"sites/{site}/mces/{m}/hostedClusters/{name}.{ext}", body)
for n_ in ("hc01", "hc02", "hc04", "hc05"):
    hc("site1", MP, f"ocp4-prod-{n_}-site1", tag("4.16.27"))
hc("site1", MP, "ocp4-prod-hc09-site1", tag("4.16.27") + "day2:\n  copy_to_sigs: []\n")             # cluster file opts out of every tenant
hc("site1", MP, "ocp4-prod-hc08-site1", tag("4.16.30") + "nodepool:\n  - {name: pool-a, workertag: 4.16.30-x86_64}\n  - {name: pool-b, workertag: 4.16.27-x86_64}\n")  # mid-upgrade
hc("site1", MQ, "ocp4-prep-hc01-site1", tag("4.20.9"))
hc("site1", MQ, "ocp4-prep-hc02-site1", tag("4.20.9"))
hc("site1", MQ, "ocp4-prep-hc03-site1", tag("4.20.9"), "yml")                                      # .yml: day2 cannot read it
hc("site1", MW, "ocp4-upgrade-demo", tag("4.21.40") + "nodepool:\n" +
   "".join(f"  - {{name: demo-{z}, workertag: {w}-x86_64}}\n" for z, w in (("a", "4.21.40"), ("b", "4.21.40"), ("c", "4.21.39"))))  # last pool one step behind
for site, m in (("site4", MF), ("site5", MN)):
    for i in range(1, 6):
        hc(site, m, f"ocp4-prod-hc{i:02d}-{site}", tag("4.16.27"))

# ---------------- tenants ----------------
CH = "repourl: https://git.example/{0}.git\ntargetRevision: main\npath: charts/{0}\nprojectNamespace: {0}\n"
chart = lambda c, extra="": {f"operators/{c}/{c}.yaml": CH.format(c) + extra, f"operators/{c}/values.yaml": "enabled: true\n"}
folder = lambda site, env, mce, cluster, *charts: {f"sites/{site}/{env}/mces/{mce}/{cluster}/{c}/.gitkeep": "" for c in charts}


def tenant(*parts):
    out = {}
    for p in parts:
        out.update(p)
    return out


# a: the "almost everything" team. Onboarded (registered), missing (hc08, prep hc01, prep hc03, hc04-site5), stale (hc03), env-less names, manual folder (hc09),
#    UPI + version.yaml, exclusions, pin + a cluster on a version with no pin layer (hc02 4.20.9), defaults targetRevision over a pin (hc01), repoUrl casing,
#    site/env values, secret-ish key, defaults/hub, chart conflict with b (hc04 + in-cluster kyverno)
a = tenant(
    chart("kyverno"), {"operators/kyverno/ocp-versions/4.16.27/kyverno.yaml": "targetRevision: release-4.16.27\n"},
    chart("logging"), {"operators/logging/ocp-versions/4.16.27/logging.yaml": "targetRevision: release-4.16.27\n",
                       "defaults/hosted-clusters/logging/logging.yaml": "targetRevision: fleet-main\n",
                       "defaults/hosted-clusters/exclusions.yaml": "exclusions:\n  logging:\n    - ocp4-prod-hc05-site1\n"},
    {"operators/cluster-roles/cluster-roles.yaml": "repoUrl: https://git.example/cluster-roles.git\ntargetRevision: main\npath: charts/cluster-roles\n",   # repoUrl casing bug
     "operators/example-token/example-token.yaml": CH.format("example-token"), "operators/example-token/values.yaml": "api:\n  url: https://api.example\n  token: not-for-the-page\n  password: hunter2\n"},
    {"sites/site1/values.yaml": "registry: registry.site1.example\n", "sites/site1/prod/values.yaml": "proxy: http://proxy.site1.example:3128\n",
     "sites/site5/values.yaml": "registry: registry.site5.example\n"},
    chart("hubmon"), {"defaults/hub/hubmon/hubmon.yaml": "targetRevision: hub-main\n", "defaults/hub/values.yaml": "siteNetworks: [a, b]\n"},
    {f"sites/site1/prod/mces/{MP}/in-cluster/kyverno/.gitkeep": "", f"sites/site1/prep/mces/{MQ}/in-cluster/kyverno/.gitkeep": "", f"sites/site5/prod/mces/{MN}/in-cluster/kyverno/.gitkeep": ""},
    folder("site1", "prod", MP, "ocp4-prod-hc04-site1", "kyverno", "cluster-roles", "example-token"),
    folder("site1", "prod", MP, "ocp4-prod-hc05-site1", "kyverno"),                               # logging is excluded for hc05 -> listed as not applied
    folder("site1", "prod", MP, "ocp4-prod-hc01-site1", "kyverno", "logging"),                      # 4.16.27: pin applies; defaults override the logging pin
    folder("site1", "prod", MP, "ocp4-prod-hc02-site1", "kyverno"),
    folder("site1", "prod", MP, "ocp4-prod-hc09-site1", "kyverno"),                                  # day1 does not register it for a: onboarded by hand
    folder("site1", "prod", MP, "ocp4-prod-hc03-site1", "kyverno"),                                  # not in day1: stale
    folder("site1", "prep", MQ, "ocp4-prep-hc02-site1", "kyverno"),                                  # 4.20.9, pins only for 4.16.27: missing-version-layer
    folder("site5", "prod", MN, "ocp4-prod-hc01-site5", "kyverno"), folder("site5", "prod", MN, "ocp4-prod-hc02-site5", "kyverno"),
    folder("site5", "prod", MN, "ocp4-prod-hc03-site5", "kyverno"), folder("site5", "prod", MN, "ocp4-prod-hc05-site5", "kyverno"),
    {"sites/site1/prod/upi/ocp4-upi01-site1/kyverno/.gitkeep": "", "sites/site1/prod/upi/ocp4-upi01-site1/version.yaml": tag("4.16.27"),
     "sites/site1/prod/upi/values.yaml": "upi: true\n"})

# b: behind. Conflict with a (kyverno-dup, same source and revision as a's pinned kyverno on hc05), version skew with a (kyverno: a is on the 4.16.27 pin, b on main, on hc04 and the MCE), env mismatch (prep cluster/MCE filed under prod), lots of missing, site4/hc-only available
b = tenant(
    chart("kyverno"), chart("cert-sync"),
    {"operators/kyverno-dup/kyverno-dup.yaml": "repourl: https://Git.example/kyverno.git/\ntargetRevision: release-4.16.27\npath: charts/kyverno\n"},   # same source and effective revision as a's kyverno (OCP pin) on hc05: conflict
    {f"sites/site1/prod/mces/{MP}/in-cluster/kyverno/.gitkeep": "", f"sites/site5/prod/mces/{MN}/in-cluster/cert-sync/.gitkeep": ""},
    folder("site1", "prod", MP, "ocp4-prod-hc04-site1", "kyverno"),                                 # also in a: conflict
    folder("site1", "prod", MP, "ocp4-prod-hc05-site1", "cert-sync", "kyverno-dup"),
    folder("site1", "prod", MQ, "ocp4-prep-hc02-site1", "cert-sync"),                                # day1 env prep, folder under prod: stale
    folder("site5", "prod", MN, "ocp4-prod-hc01-site5", "cert-sync"))

# c: the site4 MCE team. hc01/hc02/hc03 registered, hc04/hc05 missing, hc04 by hand, hc05 under the wrong site (stale: moved)
c = tenant(
    chart("kyverno"), chart("logging"),
    {f"sites/site4/prod/mces/{MF}/in-cluster/kyverno/.gitkeep": "", f"sites/site1/prod/mces/{MP}/in-cluster/logging/.gitkeep": ""},
    *[folder("site4", "prod", MF, f"ocp4-prod-{n_}-site4", "kyverno") for n_ in ("hc01", "hc02", "hc03")],
    folder("site1", "prod", MP, "ocp4-prod-hc04-site1", "logging"),
    folder("site4", "prod", MF, "ocp4-prod-hc05-site1", "logging"))

# ---------------- push ----------------
org = group("org")
d2 = group("day2", org, "org/day2")
sigs = group("sigs", d2, "org/day2/sigs")
d1 = group("day1", org, "org/day1")
print("seeding", A.url)
push(project(d1, "platform-config", "org/day1/platform-config"), day1, "Seed day1 platform-config")
for name, files in (("a", a), ("b", b), ("c", c)):
    push(project(sigs, name, f"org/day2/sigs/{name}"), files, f"Seed tenant {name}")

# ---------------- branches (the picker): what a team works on besides main ----------------
# a@dev: onboards hc08 (was missing), drops the stale hc03 folder, and adds the cert-sync chart b already deploys on hc05: same source and revision = conflict with b
# c@release: onboards hc04 (was missing)
branch(project(sigs, "a", "org/day2/sigs/a"), "dev",
       tenant(folder("site1", "prod", MP, "ocp4-prod-hc08-site1", "kyverno"), folder("site1", "prod", MP, "ocp4-prod-hc05-site1", "cert-sync"), chart("cert-sync")),
       [f"sites/site1/prod/mces/{MP}/ocp4-prod-hc03-site1/kyverno/.gitkeep"], "dev: onboard hc08, drop hc03, add cert-sync")
branch(project(sigs, "c", "org/day2/sigs/c"), "release", folder("site4", "prod", MF, "ocp4-prod-hc04-site4", "kyverno"), [], "release: onboard hc04")
