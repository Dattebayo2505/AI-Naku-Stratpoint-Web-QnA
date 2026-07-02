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

## Tests

```bash
uv run pytest                 # unit tests (no network)
uv run pytest -m integration  # live smoke test against stratpoint.com
```

## Design

Crawler design spec and implementation plan live in
`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md` and
`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`.
