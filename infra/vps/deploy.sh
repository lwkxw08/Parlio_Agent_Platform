#!/usr/bin/env bash
# Render config/ from templates using infra/vps/.env, generating any missing secrets, then
# (re)build and start the stack. Idempotent; run from infra/vps on the server.
#
# IMAGE_TAG=<git sha> (exported by CI) pulls the api/voice-worker images built in CI from GHCR
# instead of building on the box and is written to .env so manual restarts reuse it; unset it
# (and remove it from .env) for a local build.
# DATABASE_URL in .env points the API at a managed Postgres; the local postgres container is
# then left out of the stack.
#
# The voice worker is rolled, not restarted: a new container is started and must register with
# LiveKit before the old one is told to drain (finish its calls, take no new ones) and exit.
# Callers never hit a "no worker available" window and live calls are not cut off.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "infra/vps/.env missing (see .env.example)"; exit 1; }

gen() { openssl rand -hex 24; }
ensure() { grep -q "^$1=.\+" .env || { sed -i "/^$1=/d" .env; echo "$1=$(gen)" >> .env; }; }
for v in LIVEKIT_API_KEY LIVEKIT_API_SECRET POSTGRES_PASSWORD POSTGRES_APP_PASSWORD \
         MINIO_ROOT_PASSWORD PARLIO_WORKER_API_KEY PARLIO_VAULT_KEY; do ensure "$v"; done

# The caller's IMAGE_TAG (CI) must win over the one persisted in .env from the previous deploy.
CALLER_IMAGE_TAG="${IMAGE_TAG:-}"
set -a; . ./.env; set +a
IMAGE_TAG="${CALLER_IMAGE_TAG:-${IMAGE_TAG:-}}"
mkdir -p config
for t in *.tmpl; do envsubst < "$t" > "config/${t%.tmpl}"; done

dc() { docker compose --env-file .env "$@"; }

if [ -n "${IMAGE_TAG:-}" ]; then
  export IMAGE_TAG
  # Persist so a later plain `docker compose up -d <svc>` keeps this image instead of :local.
  sed -i "/^IMAGE_TAG=/d" .env && echo "IMAGE_TAG=$IMAGE_TAG" >> .env
  dc pull api voice-worker || { echo "pull of $IMAGE_TAG failed; building locally" >&2; dc build; }
else
  dc build
fi
# Everything except the worker can restart in place (short blip, no live-call impact).
SKIP='^voice-worker$'
[ -n "${DATABASE_URL:-}" ] && SKIP="$SKIP|^postgres$"
dc up -d --remove-orphans --no-deps $(dc config --services | grep -Ev "$SKIP")

roll_worker() {
  local old new deadline
  old=$(dc ps -q voice-worker || true)
  if [ -z "$old" ]; then
    dc up -d --no-deps voice-worker
    return
  fi
  # Keep the old container running and add one on the new image.
  dc up -d --no-deps --no-recreate --scale voice-worker=$(( $(echo "$old" | wc -l) + 1 )) voice-worker
  new=$(comm -13 <(echo "$old" | sort) <(dc ps -q voice-worker | sort))
  echo "new worker: $new — waiting for it to register with LiveKit"
  deadline=$((SECONDS + 180))
  until docker logs "$new" 2>&1 | grep -q '"registered worker"'; do
    if [ $SECONDS -ge $deadline ] || [ "$(docker inspect -f '{{.State.Running}}' "$new")" != "true" ]; then
      echo "new worker failed to start; keeping the old one" >&2
      docker logs --tail 50 "$new" >&2 || true
      docker rm -f "$new" >/dev/null 2>&1 || true
      return 1
    fi
    sleep 2
  done
  # SIGTERM → livekit-agents drains: no new jobs, waits for active calls (up to stop_grace_period).
  for c in $old; do
    echo "draining old worker $c"
    docker stop "$c" >/dev/null
    docker rm "$c" >/dev/null
  done
}
roll_worker

dc ps
