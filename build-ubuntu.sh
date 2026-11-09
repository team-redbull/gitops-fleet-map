#!/bin/sh
# Builds gitops-fleet-map:1.3.0 from Ubuntu noble with debootstrap (the only base reachable from the sandbox: archive.ubuntu.com).
# python3 (3.12) + python3-yaml + ca-certificates, no git/pip/docs/man/apt lists. Needs root, debootstrap, docker. The Dockerfiles are the real recipe; this is the same image content.
set -eu
cd "$(dirname "$0")"
TAG=${TAG:-gitops-fleet-map:1.3.0}
R=$(mktemp -d); trap 'rm -rf "$R"' EXIT
debootstrap --variant=minbase --components=main --include=python3,python3-yaml,ca-certificates noble "$R" http://archive.ubuntu.com/ubuntu >&2
rm -rf "$R"/usr/share/doc "$R"/usr/share/man "$R"/usr/share/info "$R"/usr/share/lintian "$R"/usr/share/locale "$R"/var/lib/apt/lists/* "$R"/var/cache/apt/* "$R"/var/cache/debconf/*-old "$R"/var/log/*
mkdir -p "$R/app" "$R/data"
cp fleetmap.py index.tmpl.html "$R/app/"
chgrp 0 "$R/data" "$R/tmp" && chmod g=u "$R/data" "$R/tmp"
# no host CA may end up in the image: set HOST_CA_BUNDLE to a build-host/proxy CA bundle to check that none of its non-stock certs is in the image trust store
python3 - "$R" <<'PY'
import base64, hashlib, os, re, sys
R = sys.argv[1]
fps = lambda text: {hashlib.sha256(base64.b64decode("".join(c.splitlines()[1:-1]))).hexdigest() for c in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)}
hb = os.environ.get("HOST_CA_BUNDLE", "")
host = fps(open(hb).read()) if hb and os.path.exists(hb) else set()
stock = set()  # what Mozilla/Ubuntu ships: the host's own mozilla dir + certifi
for d, _, fs in os.walk("/usr/share/ca-certificates/mozilla"):
    for f in fs: stock |= fps(open(os.path.join(d, f), errors="ignore").read())
try:
    import certifi; stock |= fps(open(certifi.where()).read())
except ImportError:
    pass
only = host - stock
img = set()
for d in ("etc/ssl/certs", "usr/local/share/ca-certificates", "usr/share/ca-certificates"):
    for dp, _, fs in os.walk(os.path.join(R, d)):
        for f in fs:
            p = os.path.join(dp, f)
            if os.path.isfile(p): img |= fps(open(p, errors="ignore").read())
print(f"host bundle certs: {len(host)}, of which not in stock Mozilla/certifi (host-only): {len(only)}; image certs: {len(img)}", file=sys.stderr)
print(f"host-only certs found in image: {len(only & img)}", file=sys.stderr)
sys.exit(1 if only & img else 0)
PY
tar -C "$R" -c . | docker import \
  --change 'WORKDIR /app' --change 'ENV HOME=/tmp' --change 'ENV PYTHONDONTWRITEBYTECODE=1' --change 'ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
  --change 'USER 1001' --change 'EXPOSE 8080' \
  --change 'CMD ["python3","fleetmap.py","serve","--out","/data/out","--port","8080"]' - "$TAG" >/dev/null
docker image inspect "$TAG" -f 'image size: {{.Size}} bytes'
