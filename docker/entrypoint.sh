#!/bin/sh
# Auto-ingest before serving, then exec the service command (uvicorn / streamlit).
# stratpoint-rag-ingest is content_hash-gated + idempotent:
#   empty chroma volume -> embeds everything (slow, one time)
#   warm volume         -> re-embeds only changed pages (near-instant)
# Only the `api` service needs the vectors; the `ui` sets RUN_INGEST=0 to skip.
set -e

if [ "${RUN_INGEST:-1}" = "1" ]; then
    echo "[entrypoint] running stratpoint-rag-ingest (hash-gated)..."
    uv run stratpoint-rag-ingest
fi

exec "$@"
