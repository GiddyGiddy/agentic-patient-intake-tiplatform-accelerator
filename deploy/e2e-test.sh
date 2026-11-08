#!/usr/bin/env bash
# Usage: deploy/e2e-test.sh [staging]
# Authenticated end-to-end test of the running staging stack (needs the mock IdP from compose.staging.yml).
# Runs a throw-away container on the stack's internal network, logs in at the mock IdP with PKCE and drives
# chat -> consent -> ePA -> booking -> KIM through the proxy. Env: BASE, KVNR, TIMEOUT_S (see e2e_test.py).
set -euo pipefail

ENVIRONMENT="${1:-staging}"
[ "$ENVIRONMENT" = "staging" ] || { echo "only staging has the mock IdP" >&2; exit 2; }
if command -v podman >/dev/null 2>&1; then ENGINE=podman; else ENGINE=docker; fi
NETWORK="intake-${ENVIRONMENT}_internal"
HERE="$(cd "$(dirname "$0")" && pwd)"

"$ENGINE" network inspect "$NETWORK" >/dev/null 2>&1 || { echo "network $NETWORK not found; is the stack up?" >&2; exit 2; }
exec "$ENGINE" run --rm --network "$NETWORK" \
  -e BASE -e IDP -e KVNR -e TIMEOUT_S -e REDIRECT_URI \
  -v "$HERE/e2e_test.py:/e2e_test.py:ro" \
  docker.io/library/python:3.12-alpine python -u /e2e_test.py
