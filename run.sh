#!/usr/bin/env bash
# run.sh — launch the whole Stratpoint RAG chatbot (backend API + frontend UI) together.
# Linux / macOS.  Mirrors the docker-compose commands so local and container runs match.
#
# Requires: uv (https://docs.astral.sh/uv/) and a .env with NVIDIA_API_KEY (copy .envexample).
# Ports:    API 8000, UI 8501 (override with API_PORT / UI_PORT).
# Env:      RUN_INGEST=0 skips the index build/refresh (faster restarts once the index is warm).
#
#   ./run.sh
#
set -euo pipefail
cd "$(dirname "$0")"

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
export STRATPOINT_API_URL="http://localhost:${API_PORT}"

echo "==> Syncing dependencies (uv sync)"
uv sync

if [ ! -f .env ]; then
  echo "WARNING: .env not found — the API needs NVIDIA_API_KEY to answer questions."
  echo "         Copy .envexample -> .env and set NVIDIA_API_KEY. The UI will still start."
fi

if [ "${RUN_INGEST:-1}" = "1" ]; then
  echo "==> Building/refreshing the retrieval index (first run downloads the embedding model and"
  echo "    embeds the 371-page corpus — a few minutes; later runs are near-instant, hash-gated)"
  uv run stratpoint-rag-ingest
fi

# Backend in the background; make sure it's stopped whenever this script exits (incl. Ctrl+C).
echo "==> Starting backend API  -> http://localhost:${API_PORT}"
uv run uvicorn stratpoint_rag.api.app:app --host 0.0.0.0 --port "${API_PORT}" &
API_PID=$!

cleanup() {
  echo
  echo "==> Shutting down backend (pid ${API_PID})..."
  kill "${API_PID}" 2>/dev/null || true
  wait "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Frontend in the foreground — Ctrl+C here stops the UI, then the trap stops the API.
echo "==> Starting frontend UI  -> http://localhost:${UI_PORT}"
uv run streamlit run src/stratpoint_rag/ui/app.py \
  --server.port "${UI_PORT}" --server.address 0.0.0.0 --server.headless true
