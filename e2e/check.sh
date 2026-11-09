#!/bin/sh
# Live checks against the running e2e environment (up.sh first). Takes ~13 minutes: idle test, mutation test and outage test wait on the 60s interval.
set -e
E=$(cd "$(dirname "$0")" && pwd)
python3 "$E/live_check.py" static
python3 "$E/live_check.py" idle
python3 "$E/live_check.py" mutate
python3 "$E/live_check.py" outage
