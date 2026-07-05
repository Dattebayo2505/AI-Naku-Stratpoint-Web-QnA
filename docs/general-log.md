# General Log

Non-technical, report- and presentation-oriented record of what was done on this
project, **maintained by Claude Code via the `update-log` skill**.

This is raw material for a later report or presentation — milestones, decisions, and the
documents/artifacts produced. It deliberately leaves out technical mechanics (bug fixes,
branch merges, test runs). When the user asks to "update the log," the `update-log` skill
refreshes the newest entry below.

---

## 2026-07-05 — Integrated NVIDIA NeMo Guardrails as an alternative backend

**What we did**
- Created a `feat/nemo-guardrails` branch and built the NeMo integration in parallel to the existing custom pipeline, preserving both options via a `use_nemo` toggle on the agent.
- Designed a NeMo config directory (`guardrails/nemo/`) with a model config pointing at the NVIDIA NIM endpoint, Colang 2.x rails using library flows (`self check input`, `jailbreak detection heuristics`, `self check hallucination`, `self check output`), and custom Python actions for PII redaction, topic relevance, hallucination checking, and advice blocking.
- Built a `NeMoGuardrailPipeline` wrapper (`guardrails/nemo_guardrails.py`) that matches the same `run_input()`/`run_output()` interface as the existing `GuardrailPipeline`, making it a drop-in replacement, with graceful fallback when no API key is available.
- Updated the agent orchestrator (`agent/orchestrator.py`) to accept `use_nemo=True/False`, selecting the appropriate guardrail backend at init time.

**What we produced**
- NeMo Guardrails config files: `guardrails/nemo/config.yml`, `main.co` (Colang flows), `actions.py` (custom actions), `rails/disallowed.co` (topic-based disallowed flows).
- `guardrails/nemo_guardrails.py`: wrapper class with graceful fallback for keyless environments.
- Updated architecture documentation (`docs/architecture-flow.md`) with NeMo integration details.
- Updated test results (`docs/test-results.md`) with NeMo test outcomes.

**Open / to decide**
- The NeMo integration requires an active LLM call even for heuristic-only rails, unlike the custom pipeline which operates fully offline. Decide whether NeMo's library flows add enough value to offset the API key dependency.
- Wire the agent orchestrator into the API (`api/`) and UI (`ui/`) subpackages — both guardrail backends are now toggle-ready. 

## 2026-07-04 — Built the Disambiguation & Guardrails pipeline for the chatbot

**What we did**
- Analyzed the full codebase architecture — `rag/` and `prompts/` are built, while `disambiguation/`, `guardrails/`, and `agent/` were empty scaffolds awaiting implementation.
- Evaluated NVIDIA NeMo Guardrails against a custom Python approach and chose the latter — lighter weight, deterministic control, and tight integration with existing Pydantic schemas and httpx calls.
- Designed a three-phase pipeline: (1) LLM-based intent classification with heuristic fallback and multi-turn clarification loop, (2) regex-based PII redaction, topic/keyword filtering, combined LLM-judge + semantic hallucination detection, and a custom summary buffer for conversation memory, (3) an agent orchestrator that wires all layers together.
- Documented the full analysis, architecture, and implementation decisions in `stratpoint_rag_prompt.md`.
- Built a verification test suite for the RAG retrieval module, covering both normal use and failure cases.
- Resolved the local-model-sizing constraint by switching answer generation to a cloud model endpoint (NVIDIA-hosted Gemma).

**What we produced**
- The disambiguation module (`src/stratpoint_rag/disambiguation/`): intent classifier, slot extractor, multi-turn clarification loop, and router.
- The guardrails module (`src/stratpoint_rag/guardrails/`): PII redactor, topic filter, keyword blocker, output PII checker, combined hallucination checker, advice blocker, composable pipeline, and dual-memory system.
- The agent orchestrator (`src/stratpoint_rag/agent/orchestrator.py`): production entry point that runs input guardrails → disambiguation → retrieval → LLM → output guardrails → memory update.
- A working cloud-backed answer path — grounded answers using NVIDIA-hosted Gemma.

**Open / to decide**
- The disambiguation and guardrails modules need an `NVIDIA_API_KEY` for full LLM-powered classification and slot extraction; heuristic fallbacks handle the keyless case for testing.
- The next integration step is wiring the agent orchestrator into the API (`api/`) and UI (`ui/`) subpackages once those are implemented.

## 2026-07-03 — Planned the RAG and Dockerization modules

**What we did**
- Worked through a guided Q&A to pin down how Vienn's two owned modules — RAG (retrieval)
  and Dockerization (packaging/deploy) — should be built, and captured the outcome in a
  planning document (`docs/plan-rag-dockerization.md`).
- Settled the RAG approach: store the corpus in an embedded Chroma vector database, split
  each page into ~800-token overlapping chunks, and answer questions by retrieving the top
  few most-relevant chunks with their source links so replies cite stratpoint.com pages.
- Decided the RAG module's job stops at handing back relevant passages (a `retrieve`
  function the chatbot's agent will call), with a thin throwaway wrapper only so retrieval
  can be tested and demoed before teammates' agent is ready — keeping module ownership clean.
- Chose to embed the pages automatically when the app container starts (backed by a
  re-runnable command that only re-processes pages whose content changed), and to keep the
  heavy web-crawler out of the deployed image since the app only reads the finished corpus.
- Left the vector search and page-embedding models designed as swappable between a local
  (offline, on-container) option and a cloud option, so the group can decide later without
  reworking the pipeline.

**What we produced**
- A planning document for the RAG and Dockerization modules (`docs/plan-rag-dockerization.md`)
  with a decision log, build order, and rubric/presentation talking points.

**Open / to decide**
- Which model provider to use for generation and for embeddings (local Ollama/gemma vs a
  cloud endpoint like Google AI Studio or NVIDIA NIM) — both routes documented, group undecided.
- The final Docker Compose layout (which containers run together) — deferred until the full
  app is assembled and its behavior on the Proxmox/LXC host is known.

---

## 2026-06-26 — Added incremental refresh to the crawler

**What we did**
- Set out to make re-crawls cheap: refresh only the pages that changed since the last run
  instead of re-scraping the whole site every time.
- Chose the sitemap's "last modified" date as the change signal — a page is skipped when
  its date is unchanged — with a manual full-refresh override for the cases where the site
  doesn't update that date reliably. Rationale and approach captured in the design spec
  (`docs/superpowers/specs/2026-06-14-incremental-crawl-design.md`).
- Decided to keep the corpus complete and non-destructive: pages that disappear from the
  site are retained and flagged rather than deleted, so nothing is lost silently.
- Wrote a step-by-step implementation plan
  (`docs/superpowers/plans/2026-06-14-incremental-crawl.md`) and built against it.

**What we produced**
- An incremental refresh mode: re-running over the 371-page corpus with nothing changed
  now completes in ~1.5 seconds (skipping all 371 pages), versus the ~5-minute full crawl.
  Verified end-to-end against the live site, including the full-refresh override.

**Open / to decide**
- Removed pages are currently kept and flagged; decide later whether to prune them from the
  corpus so retrieval never serves pages that no longer exist on the site.

---

## 2026-06-14 — Designed and built the stratpoint.com crawler

**What we did**
- Brainstormed the crawler's design through a guided Q&A and captured the agreed approach
  in a design spec (`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md`).
- Chose a sitemap-driven approach (read the site's published sitemap) over a
  link-following crawler, because stratpoint.com exposes a complete WordPress sitemap —
  simpler and more reliable coverage.
- Decided to extract only each page's main content, dropping site navigation, footers,
  and related-content widgets, so the corpus is clean article text.
- Turned the spec into a step-by-step implementation plan
  (`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`) and built against it.

**What we produced**
- A 371-page Markdown corpus of stratpoint.com (one file per page) plus a manifest,
  ready to feed the RAG pipeline.
- Project documentation: `README.md` and `CLAUDE.md`.

**Open / to decide**
- 8 pages came out near-empty (e-book download forms and internal test pages). Decide
  whether to keep or drop them from the corpus before embedding.

---
