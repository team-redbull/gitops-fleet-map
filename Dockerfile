# Default image: Red Hat UBI 9 minimal + Python 3.12 + PyYAML. No git, bash or curl. Suits disconnected environments.
# Mirror the base and point pip at your index:  docker build --build-arg BASE=registry.example/ubi9/ubi-minimal:9.5 --build-arg PIP_INDEX_URL=https://mirror.example/api/pypi/pypi/simple -t gitops-fleet-map .
ARG BASE=registry.access.redhat.com/ubi9/ubi-minimal:9.5
FROM ${BASE}
ARG PIP_INDEX_URL
# hadolint ignore=DL3041
RUN microdnf install -y --nodocs --setopt=install_weak_deps=0 python3.12 python3.12-pip \
 && pip3.12 install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} PyYAML==6.0.2 \
 && microdnf remove -y python3.12-pip \
 && microdnf clean all \
 && rm -rf /var/cache/dnf /var/cache/yum /root/.cache
WORKDIR /app
COPY fleetmap.py index.tmpl.html ./
# OpenShift runs with an arbitrary UID in group 0: the writable dirs are group 0, g=u
RUN mkdir -p /data && chgrp 0 /data /tmp && chmod g=u /data /tmp
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 1001
EXPOSE 8080
CMD ["python3.12", "fleetmap.py", "serve", "--out", "/data/out", "--port", "8080"]
