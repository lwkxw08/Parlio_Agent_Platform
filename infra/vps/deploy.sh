#!/usr/bin/env bash
# Render config/ from templates using infra/vps/.env, generating any missing secrets, then
# (re)build and start the stack. Idempotent; run from infra/vps on the server.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "infra/vps/.env missing (see .env.example)"; exit 1; }

gen() { openssl rand -hex 24; }
ensure() { grep -q "^$1=.\+" .env || { sed -i "/^$1=/d" .env; echo "$1=$(gen)" >> .env; }; }
for v in LIVEKIT_API_KEY LIVEKIT_API_SECRET POSTGRES_PASSWORD POSTGRES_APP_PASSWORD \
         MINIO_ROOT_PASSWORD PARLIO_WORKER_API_KEY PARLIO_VAULT_KEY; do ensure "$v"; done

set -a; . ./.env; set +a
mkdir -p config
for t in *.tmpl; do envsubst < "$t" > "config/${t%.tmpl}"; done

docker compose --env-file .env up -d --build --remove-orphans
docker compose ps
