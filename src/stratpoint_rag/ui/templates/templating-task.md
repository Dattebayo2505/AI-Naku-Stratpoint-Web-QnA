# Proposal Templating & PDF Generation Roadmap

This task tracker outlines the end-to-end implementation for dynamic proposal generation: from **Pydantic Schema validation** and **Jinja2 variable injection** to **Playwright headless HTML-to-PDF rendering**, backend agent tools, and Streamlit UI download integration.

---

## 1. Pydantic Schema & Jinja2 Template Pipeline

- [ ] 1.1. Create typed Pydantic models: `LineItem`, `MilestoneItem`, and `ProposalQuoteContext`.
- [ ] 1.2. Implement `@computed_field` properties for automatic currency formatting, numeric subtotals, tax calculation, and grand totals.
- [ ] 1.3. Adapt HTML templates (`quote-template-c.html`) to Jinja2 syntax (`{% for %}`, `{% if %}`).
- [ ] 1.4. Build template rendering helper (`render_quote_html()`) to safely inject validated context into templates.
- [ ] 1.5. Add Jinja2 custom filters (e.g. `currency_format`, `date_format`, `slugify`) for flexible template styling.
- [ ] 1.6. Create unit tests in `tests/test_quote_template.py` to verify required fields, validation errors, and Jinja2 rendering correctness.

---

## 2. Playwright Headless HTML-to-PDF Generation Service

- [ ] 2.1. Create `pdf_service.py` (in `src/stratpoint_rag/pdf_gen/` or backend module) leveraging Playwright's Chromium engine.
- [ ] 2.2. Implement async and sync functions: `generate_pdf_from_html(html_str, output_path, options)`.
- [ ] 2.3. Configure standard A4 print parameters (`format="A4"`, `print_background=True`, zero external margin to respect template `@page` CSS).
- [ ] 2.4. Add asset resolution handling (local SVGs, brand logos, embedded web fonts) so offline rendering does not stall.
- [ ] 2.5. Implement timeout guardrails and error handling with `tenacity` retry on browser launch failures.
- [ ] 2.6. Add test cases verifying multi-page PDF generation (page count == 2, valid PDF-1.4 header, non-zero file size).

---

## 3. Backend Agent & API Integration

- [ ] 3.1. Update `generate_proposal_pdf` in `src/stratpoint_rag/agent/tools.py` to replace the `%PDF-1.4 Mock Proposal` stub with the real Playwright PDF pipeline.
- [ ] 3.2. Map agent estimation outputs (`EstimationResult`, `ExtractedRequirements`) into `ProposalQuoteContext`.
- [ ] 3.3. Ensure storage directory management saves generated PDFs into `data/proposals/<session_id>/<proposal_id>.pdf`.
- [ ] 3.4. Create FastAPI proposal download endpoint `GET /proposals/{session_id}/{proposal_id}.pdf` with `FileResponse`.
- [ ] 3.5. Register PDF generation telemetry and token metrics under `/metrics`.

---

## 4. Frontend Streamlit UI Integration

- [ ] 4.1. Add PDF proposal state tracker in `src/stratpoint_rag/ui/state.py` (`proposal_pdf_path`, `proposal_download_url`).
- [ ] 4.2. Render a "Download PDF Proposal" button in Streamlit when the agent completes a proposal task.
- [ ] 4.3. Implement an expandable in-app HTML/PDF previewer (`st.components.v1.html` or embedded PDF iframe).
- [ ] 4.4. Add user feedback toast notifications for PDF generation progress and download ready state.

---

## 5. Verification & End-to-End Testing

- [ ] 5.1. Run end-to-end integration test: Brief upload $\rightarrow$ Requirement Extraction $\rightarrow$ Cost Scoping $\rightarrow$ Playwright PDF creation.
- [ ] 5.2. Verify page breaks: check that Page 1 (Cost & Scope) and Page 2 (Roadmap & Terms) remain cleanly split on A4 export.
- [ ] 5.3. Perform load/concurrency test for simultaneous PDF generation requests in headless Chromium.
- [ ] 5.4. Document usage examples in `README.md` and update `docs/general-log.md`.
