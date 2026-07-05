#!/usr/bin/env bash
# Dev-only DB reset (Q3 decision: reset strategy during dev, alembic deferred to prod).
# Drops + recreates the app database, installs pgvector extension, re-runs schema init.
# DO NOT run in production. Requires passwordless sudo for postgres OR run interactively.
set -euo pipefail

DB_NAME="${DB_NAME:-news_aggregator}"
DB_USER="${DB_USER:-news}"

echo "[reset] dropping + recreating database '$DB_NAME' (owner: $DB_USER)"
sudo -u postgres dropdb --if-exists "$DB_NAME"
sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector; GRANT ALL ON SCHEMA public TO $DB_USER;"

echo "[reset] re-initializing ORM schema"
cd "$(dirname "$0")/.."
.venv/bin/python -u -m app.db.init_schema

echo "[reset] running smoke tests"
.venv/bin/python -m pytest tests/ -v 2>&1 | tail -8
echo "[reset] done"