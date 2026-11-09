#!/bin/sh
# Tears everything down (not run by the e2e itself: the environment is left up on purpose).
E=$(cd "$(dirname "$0")" && pwd)
pkill -f "svc/gitops-fleet-map 18080"
k3s kubectl delete deploy,svc gitops-fleet-map --ignore-not-found; k3s kubectl delete secret gitops-fleet-map-token --ignore-not-found
pkill -f "k3s server"; k3s-killall.sh 2>/dev/null
sh "$E/gitlab.sh" stop
rm -rf "$E/.state"
