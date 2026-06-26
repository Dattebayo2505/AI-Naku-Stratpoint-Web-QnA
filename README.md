# Stratpoint.com Crawler

Sitemap-driven Playwright crawler that turns stratpoint.com into per-page
Markdown plus an `index.jsonl` manifest for a downstream RAG pipeline.

## Setup

```bash
uv sync
uv pip install -r requirements.txt
```

## Usage

```bash
uv run stratpoint-crawler                 # full crawl into ./data
uv run stratpoint-crawler --limit 5       # smoke test
uv run stratpoint-crawler --save-html     # also archive raw HTML
uv run stratpoint-crawler --help          # all options
```

Output layout:

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

See `docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md` and
`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`.
