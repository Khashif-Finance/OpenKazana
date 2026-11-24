#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

WEB_MODE="prod"
FORCE_REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --dev) WEB_MODE="dev" ;;
    --rebuild) FORCE_REBUILD=1 ;;
  esac
done

export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./openkhazana_local.db}"
export AUTH_MODE="${AUTH_MODE:-local}"
export SCRAPERS_ENABLED="${SCRAPERS_ENABLED:-true}"
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}"
export INTERNAL_API_BASE_URL="${INTERNAL_API_BASE_URL:-http://localhost:8000}"

if [ ! -x "apps/api/.venv/bin/python" ]; then
  echo "[Setup 1/3] Creating Python virtual environment in apps/api/.venv..."
  python3 -m venv apps/api/.venv
  apps/api/.venv/bin/pip install --upgrade pip
  apps/api/.venv/bin/pip install -e apps/api
fi

if [ ! -d "apps/web/node_modules" ]; then
  echo "[Setup 2/3] Installing frontend dependencies in apps/web..."
  (cd apps/web && npm install)
fi

if [ "$WEB_MODE" = "prod" ] && { [ "$FORCE_REBUILD" = "1" ] || [ ! -f "apps/web/.next/BUILD_ID" ]; }; then
  echo "[Setup 3/3] Building fast compiled Next.js production bundle..."
  (cd apps/web && npm run build)
fi

echo "Starting OpenKhazana API (:8000, AUTH_MODE=$AUTH_MODE) and Web (:3000, $WEB_MODE mode)..."
(cd apps/api && ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000) &
API_PID=$!

if [ "$WEB_MODE" = "dev" ]; then
  (cd apps/web && npm run dev) &
else
  (cd apps/web && npx next start -H 0.0.0.0 -p 3000) &
fi
WEB_PID=$!

trap 'kill "$API_PID" "$WEB_PID" 2>/dev/null || true' EXIT INT TERM
echo "Web UI:   http://localhost:3000 ($WEB_MODE mode; pass --dev or --rebuild if needed)"
echo "API Docs: http://localhost:8000/docs"
wait
