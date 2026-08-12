# Stratpoint RAG Chatbot

A RAG (Retrieval-Augmented Generation) chatbot for [stratpoint.com](https://www.stratpoint.com).
The site is crawled into a Markdown corpus, indexed for retrieval, and served
through an agentic chatbot with an API and a chat UI.

The **crawler** lives in its own package, `stratpoint_crawl`, and is maintained
and run separately by the repo owner. The chatbot under `src/stratpoint_rag/`
is now end-to-end runnable: retrieval, prompting, disambiguation, guardrails,
the ReAct agent, telemetry, the API, and the chat UI.

**Tech stack:** Python 3.13 (uv-managed) · Playwright + selectolax + markdownify
(crawler) · ChromaDB + sentence-transformers `bge-small-en-v1.5` (retrieval) ·
NVIDIA NIM (`meta/llama-3.1-8b-instruct`) for generation · optional NeMo
Guardrails · FastAPI · Streamlit · Docker Compose.

## Project structure

```
src/
├── stratpoint_crawl/    #  sitemap-driven Playwright crawler → Markdown corpus (owner-maintained)
└── stratpoint_rag/      #  the chatbot
    ├── rag/             #  chunking, embeddings, vector store, retrieval, grounded answers
    ├── prompts/         #  prompt engineering: system prompts, few-shot, CoT
    ├── docparse/        #  uploaded client briefs (PDF/image) → Markdown transcription
    ├── disambiguation/  #  ambiguous-input detection, clarify intent before tool calls
    ├── guardrails/      #  input/output guardrails (built-in + optional NeMo)
    ├── agent/           #  ReAct agent orchestrating retrieval + tools
    ├── pdf_gen/         #  proposal quote: Pydantic context → Jinja template → Chromium PDF
    ├── llmops/          #  request telemetry: JSONL traces, latency/token/error metrics
    ├── api/             #  FastAPI endpoint
    ├── ui/              #  Streamlit chat UI
    └── evaluation/      #  prompt-ablation results and findings
```

The `stratpoint_rag` subpackages map to the project's capabilities (not
strictly 1-to-1). The handoff between the two packages is the crawled corpus:
`data/pages/*.md` + `data/index.jsonl`.

File-by-file map and dependency graph: **`docs/ARCHITECTURE.md`**. Runtime
request walkthrough with the guardrail and disambiguation policy:
**`docs/architecture-flow.md`**.

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

### With conda / mamba

Conda supplies the interpreter; the project's own deps still come from pip,
because they are declared in `pyproject.toml` rather than as conda packages.

```bash
conda create -n stratpoint-rag python=3.13   # or: mamba create ...
conda activate stratpoint-rag

pip install -e .                          # deps + the stratpoint-crawler console script
pip install pytest pytest-asyncio respx   # dev deps
playwright install chromium               # one-time browser download (required)
```

If you later add a package with a heavyweight binary dependency (PyTorch being
the usual one), install *that* through conda first — picking the CUDA/CPU build
explicitly — and then run `pip install -e .` so pip resolves around it instead
of pulling a second copy.

With pip or conda, drop the `uv run` prefix from the commands below (e.g. just
`stratpoint-crawler --limit 5` or `python -m stratpoint_crawl --limit 5`).

## Configuration

Copy `.envexample` to `.env` at the repo root and fill in what you need. Every
value is optional except `NVIDIA_API_KEY` (any LLM-backed path fails without
it); blank means "use the default in code".

| Variable | What it's for |
|---|---|
| `NVIDIA_API_KEY` | **Required.** NVIDIA NIM key for generation. One key works for any NIM model. |
| `LLM_MODEL`, `NVIDIA_BASE_URL`, `LLM_TIMEOUT` | Generation model, endpoint, and per-call timeout (default `meta/llama-3.1-8b-instruct`, 300s). |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Retrieval embedder (default: local sentence-transformers `bge-small-en-v1.5`). |
| `CHROMA_DIR`, `CHROMA_COLLECTION` | Where the vector store lives (default `./chroma_db`). |
| `STRATPOINT_API_URL` | UI → API base URL (default `http://localhost:8000`; Compose sets `http://api:8000`). |
| `LLMOPS_ENABLED`, `LLMOPS_LOG_PATH` | Telemetry toggle and JSONL trace path (default on, `llmops_traces.jsonl`). |
| `PROPOSAL_DIR`, `PROPOSAL_TTL_SECONDS` | Where generated quotes live and how long they survive (default `data/proposals`, 24h). |
| `PDF_TIMEOUT_MS`, `PDF_MAX_CONCURRENCY`, `PDF_BROWSER_ARGS`, `PDF_CHROMIUM_PATH` | Headless-Chromium render ceiling, concurrent browsers, extra launch flags, explicit browser path. |
| `PROPOSAL_COMPANY_NAME`, `PROPOSAL_COMPANY_EMAIL`, `PROPOSAL_COMPANY_WEBSITE`, `PROPOSAL_LOGO_PATH` | Branding printed on the quote. The logo is a **local path**, inlined as a data URI — the renderer blocks the network. |
| `PROPOSAL_VALID_DAYS`, `PROPOSAL_TAX_RATE_PERCENT`, `PROPOSAL_PAYMENT_TERMS` | Quote validity window, sales-tax rate (0 hides the row), and the payment schedule text. |
| `LCX_*`, `NON_ROOT_*`, `PUBLIC_IP_ADDRESS`, `PORT` | Proxmox LXC deployment credentials and allotted ports — not read by the app code. |

Defaults live in `src/stratpoint_rag/rag/config.py`, which calls `load_dotenv()`
on import, so `.env` is picked up without exporting anything in your shell.

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

- Queries are anchored before embedding: `rag/query_rewrite.py` swaps second-person pronouns
  ("who are *your* leaders?") for "Stratpoint" so the query carries an entity token. Pasted text
  over ~20 words is left alone.

## Usage — Agent + API

The `stratpoint_rag.agent` package is a **hand-rolled plain-text ReAct loop** over the NVIDIA NIM
endpoint — deliberately not LangChain/LangGraph, for deterministic parsing and no framework drift
(rationale in `src/stratpoint_rag/agent/README.md`). Its tools:

| Tool | Status |
|---|---|
| `search_stratpoint` — grounded Q&A over the corpus | live |
| `find_resource` — downloadable PDFs mined from retrieved chunks | live |
| `read_brief` — read/search what an uploaded document says | live (registered only with an attachment) |
| `extract_brief_requirements` — structured requirements from a brief | live (registered only with an attachment) |
| `estimate_cost_and_timeline` — cost/timeline/role breakdown | live (prices from `handbook.md` via `currency_calculator.py`) |
| `generate_proposal_pdf` — render the branded proposal PDF | live (`pdf_gen`: Jinja + headless Chromium) |

`estimate_cost_and_timeline` prices four base roles plus the category add-ons a brief's features
trigger (Cloud, AI/ML, Data, Security, licences), at the handbook's PHP rates converted at a fixed
60 PHP = 1 USD, **in whichever currency the brief itself was written in** — the amounts are
denominated in `EstimationResult.currency_code`, not necessarily USD. Its hours-and-phases heuristics
are still the original skeleton's, so the body keeps its `# TODO(teammate - …)` marker; swapping in
a fuller calculator means replacing the body only — the loop and the API are unchanged.
`stratpoint_rag.api` serves the agent over HTTP,
wrapped by `run_with_guardrails()` (input guardrails → disambiguation → answer → output guardrails).

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

## Usage — chat UI

```bash
uv run streamlit run src/stratpoint_rag/ui/app.py   # -> http://localhost:8501
```

Expects the API at `STRATPOINT_API_URL` (default `http://localhost:8000`). The sidebar shows API
health, a client-brief uploader, the session ID, a conversation reset, and a reasoning toggle; each assistant turn carries
an "Under the hood" panel with sources, the agent trace, reasoning, and grounding status. The
repo-root `run.ps1` / `run.bat` / `run.sh` launchers start the API and UI together and kill both
process trees on stop.

## Usage — client-brief upload (document parsing)

Drop a PDF or image into the sidebar's **Client brief** section and confirm; a
complete Markdown transcription is produced and shown in an expander. Accepted:
`pdf`, `png`, `jpg`, `jpeg`, `webp`, `tiff`. Export decks and Word documents to
PDF first — `.pptx`/`.docx` are rejected on purpose (see `CLAUDE.md`).

The same thing over HTTP:

```bash
# 1. store + validate (sub-second, no model runs)
curl -s -F "file=@client-brief.pdf" -F "session_id=demo" \
     http://localhost:8000/upload
# -> {"upload_id":"...","filename":"client-brief.pdf","pages":12,"sha256":"...","cached":false}

# 2. transcribe (hop 1) — seconds to a couple of minutes depending on page count
curl -s -X POST "http://localhost:8000/upload/<upload_id>/parse?session_id=demo"
# -> {"markdown_path":"...","pages_total":12,"pages_parsed":11,"pages_failed":[7],...}

# 3. read the transcription back (what the UI's "View transcription" panel fetches)
curl -s "http://localhost:8000/upload/<upload_id>/transcription?session_id=demo"

# 4. drop it
curl -s -X DELETE "http://localhost:8000/upload/<upload_id>?session_id=demo"
```

Pages that already carry a text layer are read directly and cost **no** model
call, so a fully digital 30-page RFP transcribes in seconds; only scans, images,
and diagram pages go to the vision model. `pages_failed` reports pages the model
could not read — check it before acting on anything derived from the document.

Re-uploading identical bytes in the same session returns the original
`upload_id` with `cached: true` and reuses the existing transcription.

> Uploaded briefs are stored under `data/uploads/` (gitignored), wiped when the
> API restarts, and swept after `UPLOAD_TTL_SECONDS`. The transcription is
> verbatim by design, so a brief containing injected instructions is transcribed
> faithfully — treat uploaded content as untrusted.

## Usage — proposal PDF

Ask the assistant for a proposal or a quote for an uploaded brief. The agent
extracts requirements, estimates cost and timeline, then renders a two-page A4
proposal — page 1 cost & scope, page 2 roadmap & terms — which appears in the
chat as **Download PDF proposal** with an inline preview.

The renderer is headless Chromium via Playwright, so it needs the same one-time
browser download the crawler does:

```bash
uv run playwright install chromium     # or: playwright install chromium
```

Over HTTP, the PDF path comes back on the chat response under
`proposal_data.pdf.download_url`:

```bash
curl -s -X POST http://localhost:8000/chat \
     -H 'content-type: application/json' \
     -d '{"message":"put together a proposal for this brief","session_id":"demo","attachments":["<upload_id>"]}' \
  | jq .proposal_data.pdf
# -> {"pdf_path":"...","file_size_bytes":114574,"download_url":"/proposals/demo/<proposal_id>.pdf","status":"success"}

curl -s -o proposal.pdf http://localhost:8000/proposals/demo/<proposal_id>.pdf
curl -s          http://localhost:8000/proposals/demo/<proposal_id>.html   # the preview twin
curl -s -X DELETE http://localhost:8000/proposals/demo                     # drop the session's quotes
```

Rendering to a PDF directly, without the agent:

```python
from datetime import date
from stratpoint_rag.pdf_gen import build_quote_context, render_quote_html, generate_pdf_from_html

context = build_quote_context(
    proposal_id="demo0001",
    estimation=estimation_result,          # agent.contracts.EstimationResult
    requirements=extracted_requirements,   # docparse.schema.ExtractedRequirements (optional)
    client_name="Northwind Retail",        # None is fine — the quote stays generic
    project_name="Loyalty Programme Platform",
    today=date.today(),
)
generate_pdf_from_html(render_quote_html(context), "proposal.pdf")
```

Branding, tax rate, validity window and payment terms come from the environment
(see **Configuration** and `.envexample`); the template itself lives at
`src/stratpoint_rag/pdf_gen/templates/quote-template-c.html`.

> Generated quotes are stored under `data/proposals/<session_id>/` (gitignored),
> wiped when the API restarts, and swept after `PROPOSAL_TTL_SECONDS`. A quote
> carries a client's name and their price — the download endpoint is scoped to
> the session that generated it.

## Usage — LLMOps metrics

Every `/chat` request appends one JSONL trace line (latency, model, token usage, tool calls,
grounding, error) — on success *and* on failure. Query text is deliberately never written.

```bash
curl -s http://localhost:8000/metrics | jq .aggregates
# { "count", "latency_p50_ms", "latency_p95_ms", "total_tokens", "avg_tokens", "error_rate" }
```

`GET /metrics` also returns the 50 most recent records, newest first. Set `LLMOPS_ENABLED=0` to
turn the sink off, or `LLMOPS_LOG_PATH` to move the file (default `llmops_traces.jsonl`).

## Usage — cost calculator (standalone)

`util/website_calculator.py` is a stdlib-only website-design cost estimator. It is **not** wired
into either package. It was the reference pricing logic while `estimate_cost_and_timeline` was a
stub; the live tool prices from `handbook.md` via `src/stratpoint_rag/currency_calculator.py`
instead, so this script is now a sanity check rather than the source of truth.

```bash
python util/website_calculator.py                          # interactive prompts
python util/website_calculator.py --pages 12 --style adv   # or --json for machine-readable output
```

`util/count_tokens.py` is the other standalone helper — it counts NIM tokens for one or more files,
which is how the docparse token budgets were sized. Unlike the calculator it hits the NIM endpoint,
so it needs `NVIDIA_API_KEY` in `.env`.

```bash
python util/count_tokens.py -i data/pages/about-us.md data/pages/careers.md
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

A bare-metal (no Docker) deployment path for the 6GB LXC is written up in
`docs/deploy-lxc-6gb-no-docker.md`.

## Tests

```bash
uv run pytest                 # unit tests (no network)
uv run pytest -m integration  # live smoke test against stratpoint.com
```

`tests/test_pdf_service.py` launches a real headless Chromium — offline, but it
needs the browser. Without `playwright install chromium` the module **skips**
rather than failing, so run it explicitly after touching the template or the
renderer:

```bash
uv run pytest tests/test_pdf_service.py -v   # must not report SKIPPED
```

## Design

| Document | What it covers |
|---|---|
| `docs/ARCHITECTURE.md` | File-by-file map of `stratpoint_rag`, dependency graph, data artifacts, invariants |
| `docs/architecture-flow.md` | The same system as a runtime request flow, with guardrail and disambiguation policy |
| `src/stratpoint_rag/agent/README.md` | Why a hand-rolled ReAct loop; the proposal tool contracts |
| `src/stratpoint_rag/evaluation/PROMPT_ENGINEERING_FINDINGS.md` | The prompt-variant ablation and why `v4_combined_lowtemp` won |
| `docs/deploy-lxc-6gb-no-docker.md` | Bare-metal deployment on the Proxmox LXC |
| `docs/superpowers/specs/` + `docs/superpowers/plans/` | Per-feature design specs and implementation plans (crawler, incremental crawl, agent/API, reasoning toggle, portable ReAct loop, eval foundation) |
