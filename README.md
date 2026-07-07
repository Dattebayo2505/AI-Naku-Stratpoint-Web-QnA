# Stratpoint RAG Chatbot

A RAG (Retrieval-Augmented Generation) chatbot for [stratpoint.com](https://www.stratpoint.com).
The site is crawled into a Markdown corpus, indexed for retrieval, and served
through an agentic chatbot with an API and a chat UI.

The **crawler is the component built so far** and lives in its own package,
`stratpoint_crawl` — it is maintained and run separately by the repo owner.
The chatbot itself is scaffolded under `src/stratpoint_rag/` and built out
incrementally.

## Project structure

```
src/
├── stratpoint_crawl/    #  sitemap-driven Playwright crawler → Markdown corpus (owner-maintained)
└── stratpoint_rag/      #  the chatbot
    ├── rag/             #  chunking, embeddings, vector store, retrieval
    ├── prompts/         #  prompt engineering: system prompts, few-shot, CoT
    ├── disambiguation/  #  ambiguous-input detection, clarify intent before tool calls
    ├── guardrails/      #  input/output guardrails
    ├── agent/           #  ReAct agent orchestrating retrieval + tools
    ├── api/             #  HTTP API endpoint (e.g. FastAPI)
    ├── ui/              #  Streamlit chat UI
    └── evaluation/      #  retrieval / answer-quality evals
```

The `stratpoint_rag` subpackages map to the planned capabilities (not
strictly 1-to-1): prompt engineering, disambiguation, RAG, guardrails, ReAct
agent, API endpoint, and a Streamlit chat UI. The handoff between the two
packages is the crawled corpus: `data/pages/*.md` + `data/index.jsonl`.

## Setup

### With uv (preferred)

```bash
uv sync                              # install deps from uv.lock
uv run playwright install chromium   # one-time browser download (required)
```

### Without uv (plain pip + venv)

```bash
python -m venv .venv
# activate: source .venv/bin/activate    (macOS/Linux)
#           .venv\Scripts\Activate.ps1   (Windows PowerShell)

pip install -e .                          # deps + the stratpoint-crawler console script
pip install pytest pytest-asyncio respx   # dev deps (or: pip install --group dev  on pip >= 25.1)
playwright install chromium               # one-time browser download (required)
```

With pip, drop the `uv run` prefix from the commands below (e.g. just
`stratpoint-crawler --limit 5` or `python -m stratpoint_crawl --limit 5`).

## Usage — crawler (owner-run)

```bash
uv run stratpoint-crawler                 # full crawl into ./data
uv run stratpoint-crawler --limit 5       # smoke test
uv run stratpoint-crawler --incremental   # only recrawl pages whose sitemap lastmod changed
uv run stratpoint-crawler --save-html     # also archive raw HTML
uv run stratpoint-crawler --help          # all options
```

Output layout (`data/` is gitignored):

- `data/pages/<slug>.md` — Markdown with YAML frontmatter
- `data/index.jsonl` — one record per page (url, slug, hash, status, ...)
- `data/raw_html/<slug>.html` — only with `--save-html`
- `data/run_report.json` — summary (succeeded, failed, thin-content, elapsed)

## Usage — RAG retrieval (agent / other modules)

The `stratpoint_rag.rag` package turns the crawled corpus into a searchable vector index and
exposes retrieval for the ReAct agent and the other chatbot modules.

**One-time setup after cloning** — the vector store is *not* committed; it's rebuilt from `data/`:

```bash
uv sync                        # installs deps (adds chromadb + sentence-transformers)
uv run stratpoint-rag-ingest   # embeds data/ into ./chroma_db (downloads a ~130MB model, ~a few min)
```

Then retrieve from any module:

```python
from stratpoint_rag.rag.retrieve import retrieve

for c in retrieve("Does Stratpoint do mobile app development?", k=5):
    print(c.score, c.title, c.url)   # each Chunk has: .text .url .title .score .slug
```

- `retrieve(query, k)` is the seam the **ReAct agent** calls as a retrieval tool. It needs **no
  LLM** — only a local embedding model. Grounded answer *generation* is a separate concern.
- **Gotcha:** if you skip `stratpoint-rag-ingest`, `retrieve()` returns an **empty list** (it does
  not error) — an empty result usually just means the index was never built.
- Re-run `stratpoint-rag-ingest` after a fresh crawl; it re-embeds only pages whose content changed.

Full design, config, and the embedding/LLM route options live in `docs/plan-rag-dockerization.md`.

## Usage — Agent + API

The `stratpoint_rag.agent` package is a ReAct agent (LangChain `create_react_agent` over the
NVIDIA NIM endpoint) with two tools — `search_stratpoint` (grounded Q&A) and `find_resource`
(downloadable PDFs from the corpus). `stratpoint_rag.api` serves it over HTTP.

Build the retrieval index first (one-time; regenerated from `data/`):

```bash
uv run stratpoint-rag-ingest
```

Serve the chatbot API (requires `NVIDIA_API_KEY` in `.env`):

```bash
uv run uvicorn stratpoint_rag.api.app:app --port 8000
```

Then POST a message and read the reply. The response shape is:
`{ "answer", "citations": [{title,url}], "resources": [{title,url}], "trace": [...] }`.

**Linux / macOS (bash/zsh)** — capture once, then pull fields with [`jq`](https://jqlang.github.io/jq/):

```bash
curl -s http://localhost:8000/chat -H "Content-Type: application/json" -d '{"message":"Do you have a downloadable PDF report about business process automation?"}' > reply.json
jq -r '.answer' reply.json                                # the answer text
jq -r '.resources[] | "- \(.title): \(.url)"' reply.json  # downloadable resources
```

**PowerShell**   
Capture the
object into `$r`, then access properties directly — `Format-List` truncates long strings, so
`$r.answer` prints the full text:

```powershell
$r = Invoke-RestMethod -Uri http://localhost:8000/chat -Method Post -ContentType 'application/json' -Body '{"message":"Do you have a downloadable PDF report about business process automation?"}'
$r.answer                                          # full answer text (untruncated)
$r.resources | Format-Table title, url -AutoSize   # downloadable resources
```

## Usage — Docker (whole app, single command)

Runs the API and the chat UI together via Docker Compose on one machine. Cloud LLM (NVIDIA NIM)
means no model container or GPU is needed; embeddings run locally inside the image.

**Prerequisites:** `data/` present (owner-run crawl) and a `.env` filled from `.envexample` —
at minimum set `NVIDIA_API_KEY`.

```bash
docker compose up --build     # builds the image, auto-ingests the corpus, then serves
# UI  -> http://localhost:8501
# API -> http://localhost:8000   (POST /chat, GET /health)
```

- **First boot is the slow one** — it embeds the 371-page corpus into a persisted `chroma`
  volume and caches the embedding model. Both persist, so later boots are fast. **Warm the
  volume once during setup** so a live demo never boots cold.
- The `api` service auto-ingests on start (`content_hash`-gated, so it's a near-instant no-op
  once the volume is warm); the `ui` talks to it over the compose network at `http://api:8000`.
  While the API is still ingesting on a cold boot, the UI comes up immediately and shows
  "API: Unreachable" in its sidebar, then flips to "Connected" once the API is ready.
- Corpus is bind-mounted **read-only** (`./data`); the crawler and Playwright are not in the image.
- **Run via Compose, not a bare `docker run`** — the corpus and vector volumes are wired up by
  `docker-compose.yml`. A plain `docker run` has no corpus mounted, so retrieval silently returns
  nothing and answers come back ungrounded.

Design and decisions for this setup live in `docs/plan-dockerization.md`.

## Tests

```bash
uv run pytest                 # unit tests (no network)
uv run pytest -m integration  # live smoke test against stratpoint.com
```

## Design

Crawler design spec and implementation plan live in
`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md` and
`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`.
