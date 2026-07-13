#!/bin/sh
# Assembles DATABASE_URL from individual secret fields injected by ECS Secrets.
# Keeping them separate in Secrets Manager avoids a single secret containing
# both host and credentials in plaintext — and lets us rotate the password
# without also rotating the host/port/dbname values.
# NEVER bake API keys or credentials into this script or the image.
set -e

export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "[entrypoint] Running alembic upgrade head..."
alembic upgrade head

echo "[entrypoint] Starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
