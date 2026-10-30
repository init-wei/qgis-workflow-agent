#!/usr/bin/env bash
set -euo pipefail
qwa_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$qwa_root"
# mise exec makes the pinned Node available without changing the user's global PATH.
node_run() {
  if command -v mise >/dev/null 2>&1; then
    mise exec -- npm --prefix ui/frontend "$@"
  else
    npm --prefix ui/frontend "$@"
  fi
}
case "${1:-help}" in
  install)
    uv sync --locked
    node_run ci --no-audit --no-fund
    ;;
  prepare) uv run --locked python scripts/prepare_dev.py ;;
  db)
    docker compose up -d --wait postgres
    uv run --locked python scripts/setup_database.py
    ;;
  db-stop) docker compose stop postgres ;;
  qgis)
    uv run --locked python scripts/prepare_dev.py
    export QWA_ROOT="$qwa_root"
    exec qgis --nologo --noversioncheck --noplugins \
      --profiles-path "$qwa_root/.runtime/qgis" --profile qwa-v1 \
      --code "$qwa_root/scripts/qgis_startup.py"
    ;;
  mcp)
    # Direct ASGI entry uses the same app but binds only to loopback.
    exec uv run --locked --env-file .env uvicorn \
      qgis_mcp_server.qgis_mcp_server:mcp.streamable_http_app --factory \
      --host 127.0.0.1 --port 8000
    ;;
  backend)
    exec uv run --locked --env-file .env uvicorn ui.backend.main:app \
      --host 127.0.0.1 --port 9000
    ;;
  frontend) node_run run dev -- --host 127.0.0.1 ;;
  build) node_run run build ;;
  typecheck) node_run run typecheck ;;
  test) exec uv run --locked pytest ;;
  test-db) exec uv run --locked pytest -o addopts= -m database -q ;;
  lint) exec uv run --locked ruff check . ;;
  docs) exec uv run --locked python scripts/check_docs.py ;;
  check)
    uv run --locked python -m compileall -q agent qgis_mcp_server ui/backend scripts
    uv run --locked ruff check .
    uv run --locked python scripts/check_docs.py
    uv run --locked pytest
    node_run run typecheck
    node_run run build
    ;;
  health)
    exec uv run --locked python scripts/health_check.py "${@:2}"
    ;;
  *)
    printf '%s\n' 'Usage: ./scripts/dev.sh {install|prepare|db|db-stop|qgis|mcp|backend|frontend|build|typecheck|test|test-db|lint|docs|check|health [flags]}'
    ;;
esac
