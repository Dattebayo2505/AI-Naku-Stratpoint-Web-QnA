# Proposal Templating & PDF Generation Roadmap

This task tracker outlines the end-to-end implementation for dynamic proposal generation: from **Pydantic Schema validation** and **Jinja2 variable injection** to **Playwright headless HTML-to-PDF rendering**, backend agent tools, and Streamlit UI download integration.

> **Status: complete.** The implementation lives in `src/stratpoint_rag/pdf_gen/`.
> The HTML templates moved there from `ui/templates/` — `pdf_gen` owns them, and
> a `pdf_gen` that reads out of `ui/` is an inverted dependency. Design rationale
> is recorded in `CLAUDE.md` under "Proposal PDF (`stratpoint_rag.pdf_gen`)";
> usage is in `README.md` under "Usage — proposal PDF".

---

## 1. Pydantic Schema & Jinja2 Template Pipeline

- [x] 1.1. Create typed Pydantic models: `LineItem`, `MilestoneItem`, and `ProposalQuoteContext`. — `pdf_gen/schema.py`
- [x] 1.2. Implement `@computed_field` properties for automatic currency formatting, numeric subtotals, tax calculation, and grand totals. — money is `Decimal`, quantised half-up at every step so the printed line totals are the exact addends of the printed subtotal.
- [x] 1.3. Adapt HTML templates (`quote-template-c.html`) to Jinja2 syntax (`{% for %}`, `{% if %}`). — the CSS is untouched; only the bindings changed.
- [x] 1.4. Build template rendering helper (`render_quote_html()`) to safely inject validated context into templates. — `pdf_gen/templating.py`, autoescape + `StrictUndefined`.
- [x] 1.5. Add Jinja2 custom filters (`currency_format`, `date_format`, `slugify`) for flexible template styling. — `pdf_gen/filters.py`; `slugify` is also what builds the proposal filename, so the two rules cannot drift.
- [x] 1.6. Create unit tests in `tests/test_quote_template.py` to verify required fields, validation errors, and Jinja2 rendering correctness. — 35 tests, no browser.

---

## 2. Playwright Headless HTML-to-PDF Generation Service

- [x] 2.1. Create `pdf_service.py` in `src/stratpoint_rag/pdf_gen/` leveraging Playwright's Chromium engine.
- [x] 2.2. Implement async and sync functions: `generate_pdf_from_html` / `agenerate_pdf_from_html`. Separate implementations, not wrappers — sync Playwright inside a running event loop raises outright.
- [x] 2.3. Configure standard A4 print parameters (`format="A4"`, `print_background=True`, zero margin + `prefer_css_page_size` so the template's `@page` CSS is not double-applied).
- [x] 2.4. Add asset resolution handling — local assets inlined as `data:` URIs (`pdf_gen/assets.py`), external requests aborted at the route layer, optional `base_dir` for relative paths.
- [x] 2.5. Implement timeout guardrails (`PDF_TIMEOUT_MS`) and `tenacity` retry on browser **launch** failure. Retrying the render itself just pays a template bug twice.
- [x] 2.6. Add test cases verifying multi-page PDF generation (page count == 2, `%PDF-1.` header, non-zero size, A4 geometry). — `tests/test_pdf_service.py`, skips with a named fix when Chromium is absent.

---

## 3. Backend Agent & API Integration

- [x] 3.1. Replaced the `%PDF-1.4 Mock Proposal` stub in `agent/tools.py` with the real pipeline. `pdf_gen` is imported lazily inside the function so `agent.tools` stays importable without a browser.
- [x] 3.2. Map `EstimationResult` / `ExtractedRequirements` into `ProposalQuoteContext`. — `pdf_gen/mapping.py`; an empty estimate raises rather than rendering a $0.00 quote.
- [x] 3.3. Storage at `data/proposals/<session_id>/<proposal_id>.pdf` (plus an `.html` twin for the preview). — `pdf_gen/store.py`; purge on boot, TTL sweep, explicit delete.
- [x] 3.4. `GET /proposals/{session_id}/{proposal_id}.pdf` with `FileResponse`, plus `.html` for the preview and `DELETE /proposals/{session_id}`.
- [x] 3.5. Telemetry under `/metrics` at path `/proposals/generate`, recorded on the request thread. No token fields — printing a PDF spends none, and zeros would dilute the per-model averages beside it.

---

## 4. Frontend Streamlit UI Integration

- [x] 4.1. PDF proposal state tracker in `ui/state.py` (`proposal_pdf_path`, `proposal_download_url`) + `remember_proposal()`.
- [x] 4.2. "Download PDF proposal" button, rendered live and replayed in the transcript. — `ui/components/proposal_download.py`.
- [x] 4.3. Expandable in-app previewer via `st.components.v1.html` over the HTML twin. A PDF `data:` URI is blocked by Chrome inside Streamlit's sandboxed iframe.
- [x] 4.4. Toast on ready, raised from `app.py` on the turn that produced it — a toast raised inside the component would pop again on every rerun.

---

## 5. Verification & End-to-End Testing

- [x] 5.1. End-to-end run: brief → requirement extraction → cost scoping → Playwright PDF, driven by a local `llama3.2` through the real ReAct loop (no NVIDIA endpoint). Produced a valid 2-page 114KB PDF.
- [x] 5.2. Page breaks verified: page 1 cost & scope, page 2 roadmap & terms, asserted in `tests/test_pdf_service.py` by page count, A4 geometry, and per-page text.
- [x] 5.3. Concurrency test — four simultaneous renders against a semaphore sized for 2, all producing valid 2-page PDFs.
- [x] 5.4. Documented in `README.md` ("Usage — proposal PDF", Configuration, Tests), `CLAUDE.md`, `.envexample`, and `docs/general-log.md`.
