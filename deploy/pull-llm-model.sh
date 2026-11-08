#!/usr/bin/env bash
# Usage: deploy/pull-llm-model.sh <staging|production> [model]
# Downloads the Ollama model into the stack's ollama volume. The running `llm` container sits on the
# internal (no-internet) network, so this uses a throw-away container on the default network that
# shares the same volume. Run once per model; the volume survives redeploys.
# Air-gapped hosts: `ollama pull` on a connected machine and copy ~/.ollama/models into the volume.
set -euo pipefail

ENVIRONMENT="${1:?environment (staging|production)}"
PROD_ENV_FILE="${PROD_ENV_FILE:-/etc/intake/${ENVIRONMENT}.env}"
MODEL="${2:-$(sed -n 's/^LLM_MODEL=//p' "$PROD_ENV_FILE" 2>/dev/null | tail -n1)}"
[ -n "$MODEL" ] || { echo "no model given and LLM_MODEL not set in $PROD_ENV_FILE" >&2; exit 2; }

if command -v podman >/dev/null 2>&1; then ENGINE=podman; else ENGINE=docker; fi
VOLUME="intake-${ENVIRONMENT}_ollama-data"
"$ENGINE" volume inspect "$VOLUME" >/dev/null 2>&1 || "$ENGINE" volume create "$VOLUME" >/dev/null

echo "== pulling $MODEL into volume $VOLUME"
"$ENGINE" run --rm -v "$VOLUME:/root/.ollama" --entrypoint sh docker.io/ollama/ollama:latest -c \
  'ollama serve >/tmp/ollama.log 2>&1 & for i in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; sleep 1; done; ollama pull "$0"' "$MODEL"

echo "== done; verify with: $ENGINE exec \$($ENGINE ps -qf name=${ENVIRONMENT}-llm) ollama list"
