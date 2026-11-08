#!/usr/bin/env bash
# Usage: deploy/deploy.sh <staging|production> <image-tag>
# Pulls the tag, backs up PostgreSQL, starts the stack, waits for health and rolls back to
# the previously deployed tag if the new one does not become healthy.
set -euo pipefail

ENVIRONMENT="${1:?environment (staging|production)}"
TAG="${2:?image tag}"

: "${REGISTRY:?REGISTRY must be set, e.g. registry.klinik.local/intake}"
STATE_DIR="${STATE_DIR:-/var/lib/intake}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/intake}"
export PROD_ENV_FILE="${PROD_ENV_FILE:-/etc/intake/${ENVIRONMENT}.env}"
export REGISTRY BACKUP_DIR
PROJECT="intake-${ENVIRONMENT}"
HEALTH_URL="${HEALTH_URL:-https://127.0.0.1/health}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-120}"
KEEP_BACKUPS="${KEEP_BACKUPS:-14}"

if command -v podman >/dev/null 2>&1; then ENGINE=podman; else ENGINE=docker; fi

# Staging runs the TI/IdP/KIM stubs on the VM unless STAGING_MOCKS=0; production never does.
if [ "$ENVIRONMENT" = "staging" ]; then STAGING_MOCKS="${STAGING_MOCKS:-1}"; else STAGING_MOCKS=0; fi
COMPOSE_FILES=(-f compose.prod.yml)
if [ "$STAGING_MOCKS" = "1" ]; then
  COMPOSE_FILES+=(-f compose.staging.yml)
  # address the browser uses (IP/DNS of this VM); set STAGING_HOST=... in the env file or the shell
  STAGING_HOST="${STAGING_HOST:-$(sed -n 's/^STAGING_HOST=//p' "$PROD_ENV_FILE" 2>/dev/null | tail -n1)}"
  export STAGING_HOST="${STAGING_HOST:-localhost}"
fi
compose() { "$ENGINE" compose -p "$PROJECT" "${COMPOSE_FILES[@]}" "$@"; }

[ -r "$PROD_ENV_FILE" ] || { echo "missing env file $PROD_ENV_FILE" >&2; exit 2; }
mkdir -p "$STATE_DIR" "$BACKUP_DIR"
TAG_FILE="$STATE_DIR/${ENVIRONMENT}.tag"
PREVIOUS_TAG="$(cat "$TAG_FILE" 2>/dev/null || true)"

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT_S))
  until curl -fsk --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then return 1; fi
    sleep 3
  done
}

diagnose() {
  echo "---- diagnostics ----" >&2
  compose ps -a >&2 || true
  echo "-- curl -vk $HEALTH_URL" >&2
  curl -vsk --max-time 5 "$HEALTH_URL" >&2 || true
  echo "-- proxy logs" >&2
  compose logs --tail 40 proxy >&2 || true
  echo "-- backend logs" >&2
  compose logs --tail 40 backend >&2 || true
  [ -r /etc/intake/tls/fullchain.pem ] && [ -r /etc/intake/tls/privkey.pem ] \
    || echo "!! TLS files /etc/intake/tls/{fullchain,privkey}.pem missing/unreadable (proxy cannot start)" >&2
}

backup_db() {
  # Only when a database container is already running (not on the very first deploy).
  if compose ps --status running postgres 2>/dev/null | grep -q postgres; then
    local out="/backups/pre-${TAG}-$(date +%Y%m%d%H%M%S).dump"
    echo "== pg_dump -> $out"
    compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f '"$out"
  else
    echo "== no running database, skipping backup (first deploy)"
  fi
}

rotate_backups() {
  { ls -1t "$BACKUP_DIR"/pre-*.dump 2>/dev/null || true; } | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f --
}

rollback() {
  if [ -z "$PREVIOUS_TAG" ]; then
    echo "!! no previous tag recorded, cannot roll back; stack left as is" >&2
    return 1
  fi
  echo "!! rolling back to $PREVIOUS_TAG"
  IMAGE_TAG="$PREVIOUS_TAG" compose up -d --remove-orphans
  IMAGE_TAG="$PREVIOUS_TAG" compose up -d --force-recreate --no-deps proxy || true
  if wait_healthy; then
    echo "rollback to $PREVIOUS_TAG healthy"
  else
    echo "!! rollback did not become healthy either, manual intervention required" >&2
  fi
}

echo "== deploying $TAG to $ENVIRONMENT (previous: ${PREVIOUS_TAG:-none})"
export IMAGE_TAG="$TAG"

# Pull every image (also nginx/postgres/ollama and the staging stubs). A failed pull is only fatal
# if an image is not already cached, which `compose up` reports.
compose pull || echo "!! some images could not be pulled; continuing with cached images" >&2

# Database first: start it (no-op if running), back it up, then migrate before the new backend starts.
compose up -d postgres
for _ in $(seq 1 30); do
  [ "$(compose ps --status running --format '{{.Health}}' postgres 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
backup_db

echo "== alembic upgrade head"
if ! compose run --rm --no-deps backend alembic upgrade head; then
  echo "!! migration failed; the previous version keeps running. Backup is in $BACKUP_DIR" >&2
  exit 1
fi

if ! compose up -d --remove-orphans; then
  echo "!! compose up failed" >&2
  rollback || true
  exit 1
fi

# nginx resolves upstream names once at start; recreated backend/frontend get new IPs, so the proxy
# must be recreated too, otherwise it answers 502 for a healthy backend.
compose up -d --force-recreate --no-deps proxy || echo "!! could not recreate proxy" >&2

if wait_healthy; then
  echo "$TAG" > "$TAG_FILE"
  echo "$PREVIOUS_TAG" > "$STATE_DIR/${ENVIRONMENT}.previous"
  rotate_backups
  echo "== $TAG is healthy on $ENVIRONMENT"
  LLM_MODEL_NAME="$(sed -n 's/^LLM_MODEL=//p' "$PROD_ENV_FILE" | tail -n1)"
  if [ -n "$LLM_MODEL_NAME" ] && ! compose exec -T llm ollama list 2>/dev/null | grep -q "^${LLM_MODEL_NAME}"; then
    echo "!! LLM model '$LLM_MODEL_NAME' is not in the ollama volume; chat will fall back or fail." >&2
    echo "   Run: deploy/pull-llm-model.sh $ENVIRONMENT" >&2
  fi
else
  echo "!! $TAG did not become healthy within ${HEALTH_TIMEOUT_S}s" >&2
  diagnose
  rollback || true
  exit 1
fi
