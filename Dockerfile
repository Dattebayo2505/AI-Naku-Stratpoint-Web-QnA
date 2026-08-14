# One image, run twice (api + ui) — see docker-compose.yml.
# Cloud LLM (NVIDIA NIM) => no model container, no GPU. Embeddings are local => torch ships
# here, pinned CPU-only via [[tool.uv.index]] in pyproject.toml. On Linux the PyPI torch pulls
# the full CUDA runtime (~2.1GB compressed, ~6GB installed) that nothing in this image loads —
# and which does not fit the 6GB LXC. Do not "fix" a torch resolution error by dropping the pin.

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
# pdf_gen/pdf_service.py drives headless Chromium to render the proposal PDF —
# the product's headline output. Without this the browser is absent from the
# image and `generate_proposal_pdf` fails at runtime while everything else looks
# healthy. (The comment this replaces predated pdf_gen and said the browser was
# never needed; that stopped being true when the proposal renderer landed.)
# --with-deps pulls the OS libraries headless Chromium needs on slim.
RUN uv run playwright install --with-deps chromium
# Image-level healthcheck so `docker run` (not just compose) reports health on the LXC.
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
# ponytail: the crawler never runs in-image — the corpus is produced offline and
# bind-mounted read-only; only the PDF renderer needs a browser.
