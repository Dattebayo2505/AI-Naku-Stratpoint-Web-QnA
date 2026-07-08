#!/usr/bin/env bash
# run.sh — launch the whole Stratpoint RAG chatbot (backend API + frontend UI) together.
# Linux / macOS.  Mirrors the docker-compose commands so local and container runs match.
#
# Requires: uv (https://docs.astral.sh/uv/) and a .env with NVIDIA_API_KEY (copy .envexample).
# Ports:    API 8000 (internal), UI 7860 (internal).  Override with API_PORT / UI_PORT.
#           These internal ports match the Proxmox host's NAT forwards for public access:
#             external 2167 -> 8000 (API),  external 2147 -> 7860 (UI).
#           7860 (not Streamlit's default 8501) is what the host forwards, so the UI must serve
#           there to be reachable from outside.  See docs/deploy-lxc-6gb-no-docker.md (Step 11).
# Env:      RUN_INGEST=0             skip the index build/refresh (faster restarts once warm).
#           STREAMLIT_RELAX_CORS=1   disable Streamlit CORS/XSRF — only if the UI hangs on
#                                    "Connecting…" through the public NAT port (try without first).
#           PUBLIC_IP                public IP for the startup URL banner (falls back to
#                                    PUBLIC_IP_ADDRESS in .env).
#           API_EXTERNAL_PORT / UI_EXTERNAL_PORT   external forwarded ports for the banner
#                                    (default 2167 / 2147).
#
#   ./run.sh
#
set -euo pipefail
cd "$(dirname "$0")"

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-7860}"
export STRATPOINT_API_URL="http://localhost:${API_PORT}"

# External (public) ports the Proxmox host forwards to the internal ports above. Informational
# only — used for the startup banner. Override to match your host's NAT rules if they differ.
API_EXTERNAL_PORT="${API_EXTERNAL_PORT:-2167}"
UI_EXTERNAL_PORT="${UI_EXTERNAL_PORT:-2147}"

# Cap thread oversubscription on small (2-core) containers unless the caller already set them.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-2}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

echo "==> Syncing dependencies (uv sync)"
uv sync

if [ ! -f .env ]; then
  echo "WARNING: .env not found — the API needs NVIDIA_API_KEY to answer questions."
  echo "         Copy .envexample -> .env and set NVIDIA_API_KEY. The UI will still start."
fi

# Public IP for the banner: explicit PUBLIC_IP wins, else pull PUBLIC_IP_ADDRESS from .env.
PUBLIC_IP="${PUBLIC_IP:-}"
if [ -z "${PUBLIC_IP}" ] && [ -f .env ]; then
  PUBLIC_IP="$(grep -E '^PUBLIC_IP_ADDRESS=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'\''\r' | xargs || true)"
fi

if [ "${RUN_INGEST:-1}" = "1" ]; then
  echo "==> Building/refreshing the retrieval index (first run downloads the embedding model and"
  echo "    embeds the 371-page corpus — a few minutes; later runs are near-instant, hash-gated)"
  uv run stratpoint-rag-ingest
fi

# Backend in the background; make sure it's stopped whenever this script exits (incl. Ctrl+C).
# `python -m uvicorn` runs whenever the uvicorn package is importable — immune to a missing
# console script on PATH (a common symptom right after a uv re-lock).
echo "==> Starting backend API  -> http://localhost:${API_PORT}  (internal)"
if [ -n "${PUBLIC_IP}" ]; then
  echo "                             http://${PUBLIC_IP}:${API_EXTERNAL_PORT}  (public, forward ${API_EXTERNAL_PORT}->${API_PORT})"
fi
uv run python -m uvicorn stratpoint_rag.api.app:app --host 0.0.0.0 --port "${API_PORT}" --workers 1 &
API_PID=$!

cleanup() {
  echo
  echo "==> Shutting down backend (pid ${API_PID})..."
  kill "${API_PID}" 2>/dev/null || true
  wait "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Optional CORS/XSRF relaxation for Streamlit behind the public NAT hop (opt-in).
STREAMLIT_EXTRA=()
if [ "${STREAMLIT_RELAX_CORS:-0}" = "1" ]; then
  STREAMLIT_EXTRA+=(--server.enableCORS false --server.enableXsrfProtection false)
fi

# Frontend in the foreground — Ctrl+C here stops the UI, then the trap stops the API.
echo "==> Starting frontend UI  -> http://localhost:${UI_PORT}  (internal)"
if [ -n "${PUBLIC_IP}" ]; then
  echo "                             http://${PUBLIC_IP}:${UI_EXTERNAL_PORT}  (public, forward ${UI_EXTERNAL_PORT}->${UI_PORT})"
fi
uv run streamlit run src/stratpoint_rag/ui/app.py \
  --server.port "${UI_PORT}" --server.address 0.0.0.0 --server.headless true \
  ${STREAMLIT_EXTRA[@]+"${STREAMLIT_EXTRA[@]}"}
