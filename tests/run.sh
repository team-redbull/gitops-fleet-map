#!/bin/sh
# Runs generate against fixtures and asserts on the output.
set -e
cd "$(dirname "$0")/.."
F=tests/fixtures
O=$(mktemp -d)
python fleetmap.py generate --out "$O/a" --sigs-dir $F/sigs --day1-dir $F/day1
python fleetmap.py generate --out "$O/b" --sigs-dir $F/sigs
cat > "$O/chk.py" <<'P'
import json, sys
a, b = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:3])
cell = lambda m, c, t: next(r for r in m["rows"] if r["name"] == c)["cells"][t]
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
ok(cell(a, "ocp4-prod-hc05-site1", "team-x")["status"] == "stale", "hc05 stale for team-x")
ok(all(cell(a, "ocp4-prod-hc06-site1", t)["status"] == "available" for t in ("team-x", "random")), "hc06 available for both")
ok(cell(a, "ocp4-prod-hc07-site1", "random")["status"] == "stale", "hc07 stale for random")
ok(len(a["conflicts"]) >= 1 and all(c["kind"] in ("conflict", "version-skew") and len(c["entries"]) >= 2 for c in a["conflicts"]), "at least one conflict, each with kind and entries")
ok(cell(a, "ocp4-upi01-site1", "team-x")["reason"].startswith("unverified"), "UPI unverified")
ok(a["banner"] is None and b["banner"], "banner only without day1")
ok(not any(c["status"] == "stale" for r in b["rows"] for c in r["cells"].values()), "no stale without day1")
P
python -I "$O/chk.py" "$O/a" "$O/b"
grep -q 'id="data"' "$O/a/index.html" && grep -q 'hc05' "$O/a/INVENTORY.md" && echo "PASS outputs exist"

# --- model edge cases (malformed files, duplicates, env mismatch, hub scope, paths) ---
python tests/edge_fixtures.py "$O/edge"
python fleetmap.py generate --out "$O/e" --sigs-dir "$O/edge/sigs" --day1-dir "$O/edge/day1"
cat > "$O/chk2.py" <<'P'
import json, sys
a, e = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:3])
cell = lambda m, c, t: next(r for r in m["rows"] if r["name"] == c)["cells"][t]
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
ok(a["warnings"] == [], "clean fixtures have no warnings")
ok(len(e["warnings"]) == 5 and any("alpha/defaults/mces/exclusions.yaml" in w for w in e["warnings"]), "malformed yaml/exclusions: warnings recorded, run did not crash")
d = cell(e, "ocp4-prod-dup-site1", "alpha")
ok(d["status"] == "stale" and d["reason"] == "folder exists twice: sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-dup-site1/, sites/site1/prod/mces/ocp4-prod-mce-site1-b/ocp4-prod-dup-site1/", "same cluster under two MCE folders is stale")
ok(cell(e, "ocp4-prod-both-site1", "alpha")["reason"].startswith("folder exists twice"), "hosted+UPI folder with one name is stale")
v = cell(e, "ocp4-prep-envy-site1", "beta")
ok(v["status"] == "stale" and "env prep" in v["reason"] and "env prod" in v["reason"], "env mismatch is stale")
ok(v["path"] == "sites/site1/prod/mces/ocp4-prep-mce-site1-a/ocp4-prep-envy-site1/", "cell path is the tenant's actual folder")
h = cell(e, "prod-hub", "alpha")["charts"]
ok(h == [dict(name="hubchart", origin="default", scope="hub")], "hub charts are default with scope hub")
ok(cell(a, "ocp4-prod-hc05-site1", "team-x")["path"] == "sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-hc05-site1/", "stale cell carries its path")
ok(cell(a, "ocp4-prod-hc06-site1", "team-x")["path"] == "sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-hc06-site1/", "available cell carries the path to create")
ok(cell(a, "ocp4-prod-hc06-site1", "team-x")["status"] == "available", "env check does not flag normal fixtures")
P
python -I "$O/chk2.py" "$O/a" "$O/e"

# --- provenance: layer stacks, effective merge, warnings, redaction (new fixture set) ---
python tests/prov_fixtures.py "$O/prov"
python fleetmap.py generate --out "$O/p" --sigs-dir "$O/prov/sigs" --day1-dir "$O/prov/day1"
python fleetmap.py generate --out "$O/h1" --sigs-dir "$O/prov/sigs" --day1-dir "$O/prov/day1" --hide-values
HIDE_VALUES=true python fleetmap.py generate --out "$O/h2" --sigs-dir "$O/prov/sigs" --day1-dir "$O/prov/day1"
cat > "$O/chk3.py" <<'P'
import json, sys
a, p, h1, h2 = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:5])
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
A, B, C, MA = "ocp4-prod-a-site1", "ocp4-prod-b-site1", "ocp4-prod-c-site4", "ocp4-prod-mce-site1-a"
cell = lambda m, c, t: next(r for r in m["rows"] if r["name"] == c)["cells"][t]
pv = lambda m, c, t, ch: cell(m, c, t)["pv"][ch]
kinds = lambda x: [w["k"] for w in x["w"]]
eff = lambda m, x, k="ve": {key: dict(val=v[0], by=x["v" if k == "ve" else "d"][v[1]][0], over=[x["v" if k == "ve" else "d"][i][0] for i in v[2]]) for key, v in m["eff"][x[k]].items()}
x = pv(p, A, "acme", "pinned")
ok((x["s"]["rev"], x["s"]["by"], x["s"]["sync"], x["s"]["prune"], kinds(x)) == ("release-4.16.27", "OCP 4.16.27 pin", "auto", True, []), "pin layer wins over team default, no warning on a pinned cluster")
ok(x["d"] == [["Team default", "operators/pinned/pinned.yaml"], ["OCP 4.16.27 pin", "operators/pinned/ocp-versions/4.16.27/pinned.yaml"],
              ["Fleet default (hosted clusters)", "defaults/hosted-clusters/pinned/pinned.yaml"], ["This cluster's chart", f"sites/site1/prod/mces/{MA}/{A}/pinned/pinned.yaml"]], "deploy config ladder order and paths (operators.yaml)")
y = pv(p, B, "acme", "pinned")
ok((y["s"]["rev"], y["s"]["by"]) == ("main", "Team default") and kinds(y) == ["missing-version-layer"] and "4.16.30" in y["w"][0]["t"] and "4.16.27" in y["w"][0]["t"], "cluster on 4.16.30 with only a 4.16.27 pin: missing-layer warning, falls back to team default")
ok(any(l == "OCP 4.16.30 pin" for l, _ in y["d"]) and "operators/pinned/ocp-versions/4.16.30/pinned.yaml" not in p["files"]["acme"], "missing version layer is still in the ladder, absent from the files table")
o = pv(p, A, "acme", "overr")
ok((o["s"]["rev"], o["s"]["by"], kinds(o)) == ("fleet-main", "Fleet default (hosted clusters)", ["defaults-overrides-pin"]), "fleet default targetRevision above an OCP pin: override warning, version source labelled")
ok("defaults-overrides-pin" not in kinds(pv(p, B, "acme", "overr")), "no override warning without a pin layer for the cluster's version")
v = pv(p, A, "acme", "vals"); e = eff(p, v)
ok(e["a"] == dict(val="cluster", by="Cluster-wide", over=["Team default", "Site site1"]), "effective value: final value, setting layer, overridden layers")
ok(e["m.x"] == dict(val="2", by="Site site1 · prod", over=["Team default"]) and e["m.y"] == dict(val="3", by="Fleet default for prod", over=["Team default"]), "site/env and values-<env> layers override team default key by key (maps deep-merge)")
ok(e["list"] == dict(val="[9]", by="Fleet default for this cluster", over=["Team default"]) and not any(k.startswith("list.") for k in e), "list is replaced by values-<cluster>.yaml, not merged")
ok(e["db.host"]["by"] == "Team default" and e["siteonly"]["by"] == "Site site1", "keys only set once keep their layer")
ok(e["db.password"]["val"] == "••• (hidden)" and e["db.password"]["by"] == "Cluster-wide", "secret-looking key is redacted but keeps its origin")
ok([l for l, _ in v["v"]] == ["Team default", "OCP 4.16.27 pin", "Site site1", "Site site1 · prod", "Fleet default (hosted clusters)", "Fleet default for prod",
   "Fleet default for this cluster", "MCE-wide", "Cluster-wide", "This cluster's chart"], "values ladder order for a hosted cluster (deployApp.yaml)")
ok([pth for _, pth in v["v"]][4:] == ["defaults/hosted-clusters/vals/values.yaml", "defaults/hosted-clusters/vals/values-prod.yaml", f"defaults/hosted-clusters/vals/values-{A}.yaml",
   f"sites/site1/prod/mces/{MA}/values.yaml", f"sites/site1/prod/mces/{MA}/{A}/values.yaml", f"sites/site1/prod/mces/{MA}/{A}/vals/values.yaml"], "values ladder paths: defaults layers, MCE-wide, cluster-wide, own chart")
ok(eff(p, v, "de")["syncPolicy.automated"]["val"] == "{}" and v["s"]["sync"] == "auto" and v["s"]["prune"] is False, "automated: {} counts as auto-sync with prune off")
m = pv(p, MA, "acme", "pinned")
ok(m["v"][-2][1] == f"sites/site1/prod/mces/{MA}/in-cluster/values.yaml" and m["v"][-3][1] == f"sites/site1/prod/mces/{MA}/values.yaml" and m["v"][6][1].endswith(f"values-{MA}.yaml") and m["d"][1][0] == "OCP 4.16.27 pin", "MCE in-cluster: clusterPath is <mce>/in-cluster, values-<mce>, MCE's own OCP version")
u = pv(p, "ocp4-upi01-site1", "acme", "pinned")
ok(u["d"][1][0] == "OCP 4.14.40 pin" and u["d"][2][0] == "Fleet default (UPI clusters)" and u["v"][-3] == ["UPI-wide", "sites/site1/prod/upi/values.yaml"] and u["v"][-1][1] == "sites/site1/prod/upi/ocp4-upi01-site1/pinned/values.yaml", "UPI: own version.yaml selects the pin, UPI-wide slot")
hb = pv(p, "prod-hub", "acme", "hubchart")
ok([l for l, _ in hb["v"]] == ["Hub-wide", "Team default", "Fleet default (hub)"] and [l for l, _ in hb["d"]] == ["Team default", "Fleet default (hub)"] and hb["v"][0][1] == "defaults/hub/values.yaml", "hub stacks")
k = pv(p, A, "acme", "kyv")
ok(kinds(k) == ["repourl-casing"] and k["s"]["repo"] is None and "empty repoURL" in k["w"][0]["t"], "repoUrl without repourl warns (renders empty repoURL)")
rk = pv(a, "ocp4-prep-mce-site1-a", "team-x", "kyverno")
ok(kinds(rk) == ["repourl-casing"], "real team-x fixture: operators/kyverno has repoUrl, flagged")
ok(kinds(pv(a, "ocp4-prep-mce-site1-a", "team-x", "example-token")) == [] if "example-token" in cell(a, "ocp4-prep-mce-site1-a", "team-x")["pv"] else True, "correct repourl: no casing warning")
ok(kinds(pv(p, A, "acme", "broken")) == ["parse-error"] and p["files"]["acme"]["operators/broken/values.yaml"]["e"] == "not valid YAML", "malformed layer: warning and error on the file")
ok("overr" not in cell(p, C, "acme")["pv"] and [(q["chart"], q["via"]) for q in cell(p, C, "acme")["excluded"]] == [("overr", "defaults/hosted-clusters/exclusions.yaml")] and cell(p, A, "acme")["excluded"] == [], "excluded fleet default listed as excluded, not as a chart")
f = next(q for q in p["sites"]["site1"]["acme"] if q["p"] == "sites/site1/values.yaml")
ok((f["n"], f["k"]) == (2, ["a", "siteonly"]) and f["ch"] > 5 and f["cl"] >= 4, "site sheet data: site values file, keys, how many charts/clusters it feeds")
ok("beta" not in p["sites"]["site1"] and [q["p"] for q in p["sites"]["site1"]["acme"]] == ["sites/site1/values.yaml", "sites/site1/prod/values.yaml"], "site files: only existing files are listed, a tenant without any is left out")
ok(p["hide_values"] is False and a["hide_values"] is False, "values shown by default")
html = open(sys.argv[5] + "/index.html").read()
ok("hunter2" not in html and "team-pw" not in html and "s3cret" not in html, "secret values never reach the page")
for h in (h1, h2):
    ok(h["hide_values"] is True and all(v[0] is None for e in h["eff"].values() for v in e.values()) and any(h["eff"].values()), "hide-values: effective values dropped, keys and origins kept")
ok(h1["eff"] == h2["eff"] and h1["rows"] == h2["rows"], "HIDE_VALUES=true behaves like --hide-values")
hs = pv(h1, A, "acme", "pinned")["s"]
ok((hs["repo"], hs["rev"], hs["by"]) == (None, None, "OCP 4.16.27 pin"), "hide-values: summary values dropped, version source kept")
hh = open(sys.argv[6] + "/index.html").read()
ok(not any(s in hh for s in ("release-4.16.27", "fleet-main", "git.example", "proxy.site1", "registry.site1")), "hide-values page contains no values")
P
python -I "$O/chk3.py" "$O/a" "$O/p" "$O/h1" "$O/h2" "$O/p" "$O/h1"

# --- conflicts (same source+revision) and version skew, available charts and available charts ---
python tests/conflict_fixtures.py "$O/cf"
python fleetmap.py generate --out "$O/c" --sigs-dir "$O/cf/sigs" --day1-dir "$O/cf/day1"
python fleetmap.py generate --out "$O/ch" --sigs-dir "$O/cf/sigs" --day1-dir "$O/cf/day1" --hide-values
cat > "$O/chk5.py" <<'P'
import json, sys
c, ch = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:3])
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
H = lambda n: f"ocp4-prod-h{n}-site1"
at = lambda m, n: [x for x in m["conflicts"] if x["cluster"] == H(n)]
row = lambda m, n: next(r for r in m["rows"] if r["name"] == H(n))
kinds = lambda n: sorted(x["kind"] for x in at(c, n))
ent = lambda n, k: next(x for x in at(c, n) if x["kind"] == k)["entries"]
ok(at(c, 1) == [], "same folder name, different repos: nothing")
x, = at(c, 2)
ok(x["kind"] == "conflict" and x["tenants"] == ["alpha", "beta"] and [(e["rev"], e["by"]) for e in x["entries"]] == [("main", "Team default")] * 2 and "main" in x["reason"], "same source and revision across tenants: conflict, entries carry revision and the layer that set it")
x, = at(c, 3)
ok(x["kind"] == "conflict" and x["tenants"] == ["alpha"] and [e["chart"] for e in x["entries"]] == ["one", "two"], "same source and revision twice in one tenant (two folder names): conflict")
x, = at(c, 4)
ok(x["kind"] == "version-skew" and [e["rev"] for e in x["entries"]] == ["main", "v1.4"] and "main, v1.4" in x["reason"] and "one owner at one version" in x["reason"], "same source, different revision: version-skew with both revisions in the reason")
ok(kinds(5) == ["conflict", "version-skew"] and [e["chart"] for e in ent(5, "conflict")] == ["m5a", "m5b"] and [e["chart"] for e in ent(5, "version-skew")] == ["m5a", "m5c", "m5b"], "mixed group (two at main, one at v1.4): conflict for the main pair and skew for the group")
x, = at(c, 6)
ok(x["kind"] == "version-skew" and [e["rev"] for e in x["entries"]] == ["HEAD", "main"] and x["entries"][0]["by"] == "template default" and "HEAD (repo default branch)" in x["reason"], "unset revision is HEAD and differs from main: skew")
x, = at(c, 7)
ok(x["kind"] == "conflict" and [e["rev"] for e in x["entries"]] == ["HEAD"] * 2 and "HEAD (repo default branch)" in x["reason"], "unset revision on both: conflict at HEAD")
x, = at(c, 8)
ok(x["kind"] == "conflict" and [e["chart"] for e in x["entries"]] == ["n8a", "n8b"], "normalisation: .git, case, trailing slash and ./ path spell one source")
ok(at(c, 9) == [], "same repo, different path: nothing")
ok(at(c, 10) == [], "empty repourl is ignored")
ok(c["conflict_counts"] == {"conflict": 5, "version-skew": 3} and len(c["conflicts"]) == 8 and [y["kind"] for y in c["conflicts"] if y["cluster"] == H(5)] == ["conflict", "version-skew"], "counts by kind; conflicts before warnings on a cluster")
ok(all(e["repourl"] is None and e["path"] is None and e["rev"] is None for y in ch["conflicts"] for e in y["entries"]) and [y["kind"] for y in ch["conflicts"]] == [y["kind"] for y in c["conflicts"]] and not any("git.example" in y["reason"] or "main" in y["reason"] for y in ch["conflicts"]), "hide-values: same conflicts, no repo, path or revision values")
av = {a["chart"]: a for a in row(c, 11)["cells"]["alpha"]["avail"]}
base = "sites/site1/prod/mces/ocp4-prod-mce-site1-a/" + H(11) + "/"
ok(not {"own1", "dflt", "excl"} & set(av) and {"extra", "lonely"} <= set(av), "available = catalog minus deployed (own + default) minus excluded defaults")
ok(av["extra"] == dict(chart="extra", rev="v1", pinned=True, pin="v1-pin", folder=base + "extra/"), "available chart: team default version, pin for the cluster's OCP version, folder to create")
ok(av["lonely"]["pinned"] is False and av["lonely"]["pin"] is None, "pin detection: a pin for another OCP version does not count")
bv = {a["chart"]: a for a in row(c, 11)["cells"]["beta"]["avail"]}
ok("bchart" in bv and bv["bchart"]["folder"] == base + "bchart/" and "extra" not in bv, "tenant without a folder: its own catalog, path includes the cluster folder")
ok(c["catalog"]["alpha"] == 18 and c["catalog"]["beta"] == 9, "catalog size per tenant")
ok(c["sites"] == {}, "no site-level files: nothing listed")
ok(all(a["rev"] is None and a["pin"] is None for r in ch["rows"] for t in r["cells"].values() for a in t.get("avail", [])), "hide-values: no versions in available charts")
P
python -I "$O/chk5.py" "$O/c" "$O/ch"

# --- fetch layer against the fake GitLab (URL parsing, SHA checks, archives, 429, hostile archives)
python tests/fetch_test.py

# --- serve before the first successful fetch (GitLab unreachable): /healthz ok, / says no data (503), no directory listing ---
mkdir -p "$O/srv/d"; SIGS_URL=http://127.0.0.1:9/g/sigs GITLAB_TOKEN=x CACHE_DIR="$O/cache" python fleetmap.py serve --out "$O/srv" --port 18999 --interval 3600 2>/dev/null &
SP=$!; sleep 2
c() { curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "localhost:18999$1"; }
[ "$(c /healthz)" = 200 ] && [ "$(c /)" = 503 ] && [ "$(c /inventory.json)" = 404 ] && [ "$(c /d/)" = 404 ] && echo "PASS serve without data: healthz 200, / 503, no listing" || { kill $SP; echo "FAIL serve without data: $(c /healthz) $(c /) $(c /inventory.json)"; exit 1; }
kill $SP

# --- day1-driven: env parsing, .yml clusters, copy_to_sigs -> expected/missing, upgrade in progress ---
python tests/day1_fixtures.py "$O/d1"
python fleetmap.py generate --out "$O/x" --sigs-dir "$O/d1/sigs" --day1-dir "$O/d1/day1"
cat > "$O/chk4.py" <<'P'
import json, sys
x, a = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:3])
row = lambda m, c: next(r for r in m["rows"] if r["name"] == c)
cell = lambda m, c, t: row(m, c)["cells"][t]
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
ok(row(x, "ocp4-upgrade-demo")["env"] == "" and row(x, "ocp4-mce-lab")["env"] == "" and row(x, "ocp4-prod-x1-site1")["env"] == "prod", "env is only prod|prep|test; other name tokens give no env")
ok(cell(x, "ocp4-upgrade-demo", "ta")["status"] == "onboarded" and cell(x, "ocp4-mce-lab", "ta")["status"] == "onboarded", "env-less day1 names are not marked stale (regression: 'upgrade' / 'mce' env)")
ok(cell(x, "ocp4-upgrade-demo", "tb")["path"] == "sites/site1/<env>/mces/ocp4-mce-lab/ocp4-upgrade-demo/", "available path for an env-less cluster has an <env> placeholder")
y = row(x, "ocp4-prep-y1-site1")
ok(y["version"] == "4.16.30" and y["cells"]["tc"]["status"] == "onboarded" and y["warns"] and ".yml" in y["warns"][0] and ".yaml" in y["warns"][0], ".yml hosted cluster is read (version, cells) and carries a warning")
ok(not row(x, "ocp4-prod-x1-site1")["warns"], "no warning on .yaml clusters")
ok(row(x, "ocp4-prod-x1-site1")["expected"] == ["ta", "tb"] and row(x, "ocp4-prod-x2-site1")["expected"] == [] and y["expected"] == ["tc"], "copy_to_sigs: configValues -> mce layer -> cluster file, lists replaced")
ok(row(x, "ocp4-upgrade-demo")["expected"] == [], "mce values narrow copy_to_sigs to []")
ok(cell(x, "ocp4-prod-x1-site1", "ta")["status"] == "onboarded" and cell(x, "ocp4-prod-x1-site1", "ta")["reg"] == "day1", "expected + folder: onboarded, registered by day1")
ok(cell(x, "ocp4-prod-x2-site1", "ta")["status"] == "onboarded" and cell(x, "ocp4-prod-x2-site1", "ta")["reg"] == "manual", "folder but not expected: onboarded, manual")
m = cell(x, "ocp4-prod-x1-site1", "tb")
ok(m["status"] == "missing" and m["path"] == "sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-x1-site1/" and "copy Job" in m["reason"], "expected + no folder: missing, with the path to create")
ok(cell(x, "ocp4-prod-x1-site1", "tc")["status"] == "available" and cell(x, "ocp4-prod-x2-site1", "tb")["status"] == "available", "not expected + no folder: still available")
ok([q["cluster"] for q in x["summary"]["tb"]["missing"]] == ["ocp4-prod-up-site1", "ocp4-prod-x1-site1"] and not any(q["cluster"] in ("ocp4-prod-x1-site1", "ocp4-prod-up-site1") for q in x["summary"]["tb"]["available"]), "missing is counted in the tenant summary and not also under available")
ok("parent MCE" in cell(x, "ocp4-prep-y1-site1", "ta")["reason"] and cell(x, "ocp4-prep-y1-site1", "ta")["status"] == "available", "parent-MCE hint kept on available")
ok(row(x, "ocp4-prod-up-site1")["workers"] == ["4.16.29"] and row(x, "ocp4-prod-x1-site1")["workers"] == [], "worker tags that differ from mastertag are listed")
ok(x["expected_on"] is True and a["expected_on"] is False, "expected_on only when day1 registers something")
ok(not any(c["status"] == "missing" for r in a["rows"] for c in r["cells"].values()), "no missing without copy_to_sigs")
P
python -I "$O/chk4.py" "$O/x" "$O/a"
grep -q 'MISSING' "$O/x/INVENTORY.md" && echo "PASS markdown has missing"
# --- in day1, not in day2: no tenant has a folder (stale / env-mismatch folders still count as a folder) ---
python tests/nid_fixtures.py "$O/nid"
python fleetmap.py generate --out "$O/n" --sigs-dir "$O/nid/sigs" --day1-dir "$O/nid/day1"
cat > "$O/chk6.py" <<'P'
import json, sys
n, b = (json.load(open(f"{d}/inventory.json")) for d in sys.argv[1:3])
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)
L = {x["cluster"]: x for x in n["not_in_day2"]}
ok(sorted(L) == sorted(["ocp4-lab-env-site1", "ocp4-prod-hc03-site1", "ocp4-prod-h1-site2", "ocp4-prod-h2-site2", "ocp4-prod-mce-site2-a", "ocp4-prod-hc09-site1"]), "not_in_day2: exactly the clusters no tenant has a folder for")
g = L["ocp4-prod-hc03-site1"]
ok(g["expected"] == ["ta", "tb"] and g["kind"] == "hosted" and g["site"] == "site1" and g["mce"] == "ocp4-prod-mce-site1-a" and g["version"] == "4.16.30"
   and g["reason"] == "day1 registers it for ta, tb, but none of their repos has the folder. The copy Job likely failed."
   and g["paths"] == {t: "sites/site1/prod/mces/ocp4-prod-mce-site1-a/ocp4-prod-hc03-site1/" for t in ("ta", "tb")}, "expected non-empty: tenants, reason, a path per tenant")
s = L["ocp4-prod-hc09-site1"]
ok(s["expected"] == [] and s["paths"] == {} and s["reason"] == "No tenant has a folder, so nothing from day2 is deployed to this cluster.", "expected empty: plain reason, no paths")
ok(L["ocp4-lab-env-site1"]["paths"]["ta"] == "sites/site1/<env>/mces/ocp4-prod-mce-site1-a/ocp4-lab-env-site1/", "unknown env: <env> placeholder in the path")
m = L["ocp4-prod-mce-site2-a"]
ok(m["kind"] == "mce" and m["expected"] == [] and "MCE" not in m["reason"], "MCE no tenant has: listed, no parent note")
ok(all("Its MCE ocp4-prod-mce-site2-a is not in day2 either." in L[h]["reason"] for h in ("ocp4-prod-h1-site2", "ocp4-prod-h2-site2")) and L["ocp4-prod-h2-site2"]["expected"] == ["tb"], "hosted cluster on a missing MCE: listed too, reason mentions the MCE")
ok("ocp4-prod-moved-site1" not in L and "ocp4-prod-ok-site1" not in L and "ocp4-prod-mce-site1-a" not in L, "clusters covered by an onboarded or stale/env-mismatch folder are not listed")
ok("not_in_day2" not in b and b["banner"], "no day1: no list")
ok("In day1, not in day2" in open(sys.argv[1] + "/INVENTORY.md").read(), "markdown lists them")
P
python -I "$O/chk6.py" "$O/n" "$O/b"
# browser tests: skipped with SKIP_UI=1 or when Playwright is not installed
if [ -z "${SKIP_UI:-}" ] && python -c "import playwright" 2>/dev/null; then
python tests/ui_test.py "$O/a" "$O/b" "$O/e" "$O/p" "$O/h1" "$O/x" "$O/c" "$O/n"
python tests/branch_ui_test.py "$O/a"
else echo "skipping UI tests (SKIP_UI set or Playwright missing)"; fi
rm -rf "$O"
