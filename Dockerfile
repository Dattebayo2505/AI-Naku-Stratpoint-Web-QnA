# One image, run twice (api + ui) — see docker-compose.yml.
# Cloud LLM (NVIDIA NIM) => no model container, no GPU. Embeddings are local => torch ships here.

# ---- deps stage: cache the heavy deps (torch via sentence-transformers) ----
FROM python:3.13-slim AS deps
RUN pip install --no-cache-dir uv
WORKDIR /app
ENV UV_FROZEN=1
COPY pyproject.toml uv.lock ./
# deps only (not the local package yet) so this layer caches across app-code edits.
# --extra nemo: the API defaults use_nemo=True and needs nemoguardrails present.
RUN uv sync --no-dev --extra nemo --no-install-project

# ---- app stage ----
FROM deps AS app
# Copy the whole src/ tree: NeMo rails config (guardrails/nemo/*.co, config.yml) is loaded
# by path at runtime and would not survive a wheel-only install.
COPY src/ ./src/
RUN uv sync --no-dev --extra nemo
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
# ponytail: playwright ships as a transitive dep but its browser is never installed and the
# crawler never runs in-image — the corpus is produced offline and bind-mounted read-only.
