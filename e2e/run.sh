#!/usr/bin/env bash
# End-to-end browser walkthrough against a fresh, throwaway database.
# Needs: local PostgreSQL (role "portal"), backend/.venv, a built frontend, Chrome, Python Playwright.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PGPASSWORD="${PGPASSWORD:-portal}"
DB=portal_e2e
psql -h localhost -U portal -d postgres -q -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
export DATABASE_URL="postgresql+psycopg://portal:${PGPASSWORD}@localhost:5432/$DB"
export SETUP_KEY="e2e-setup-key" APP_ENV=test FILES_DIR="$(mktemp -d)"
cd "$ROOT/backend"
.venv/bin/python -m alembic upgrade head >/dev/null 2>&1
.venv/bin/python -m uvicorn app.main:app --port 8000 >/tmp/e2e-api.log 2>&1 &
API=$!
cd "$ROOT/frontend"
npx vite preview --port 4173 --strictPort >/tmp/e2e-web.log 2>&1 &
WEB=$!
trap 'kill $API $WEB 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf localhost:8000/api/health >/dev/null && curl -sf localhost:4173/ >/dev/null && break; sleep 0.5; done
python3 "$ROOT/e2e/walkthrough.py" "${1:-/tmp/e2e-shots}"
