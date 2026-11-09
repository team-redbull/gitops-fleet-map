#!/bin/sh
# gitlab.sh start|stop  -- the fake GitLab (see fake_gitlab.py for why it is fake). Data and tokens live in .state/.
set -eu
E=$(cd "$(dirname "$0")" && pwd); S=$E/.state; mkdir -p "$S/gitlab"
[ -f "$S/root.token" ] || python3 -c "import secrets;print('glpat-'+secrets.token_hex(10))" > "$S/root.token"
[ -f "$S/read.token" ] || python3 -c "import secrets;print('glpat-'+secrets.token_hex(10))" > "$S/read.token"
case "$1" in
stop) pkill -f "$E/fake_gitlab.py" || true ;;
start)
  pgrep -f "$E/fake_gitlab.py" >/dev/null || nohup python3 "$E/fake_gitlab.py" "$S/gitlab" 8929 "$(cat "$S/root.token")" "$(cat "$S/read.token")" >>"$S/gitlab.log" 2>&1 &
  for i in $(seq 1 30); do curl -sf --noproxy '*' localhost:8929/-/readiness >/dev/null && exit 0; sleep 1; done; echo "gitlab did not come up" >&2; exit 1 ;;
esac
