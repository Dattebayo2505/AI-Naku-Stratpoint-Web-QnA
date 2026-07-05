# General Log

Non-technical, report- and presentation-oriented record of what was done on this
project, **maintained by Claude Code via the `update-log` skill**.

This is raw material for a later report or presentation — milestones, decisions, and the
documents/artifacts produced. It deliberately leaves out technical mechanics (bug fixes,
branch merges, test runs). When the user asks to "update the log," the `update-log` skill
refreshes the newest entry below.

---

## 2026-07-05 — Designed and built the ReAct agent + API endpoint

**What we did**
- Brainstormed the design for the two owned components — the **ReAct agent** and the **HTTP
  API** — scoped to a stratpoint.com customer-support bot, and captured it in a design spec
  (`docs/superpowers/specs/2026-07-05-react-agent-api-design.md`).
- Framed the bot's use case around two capabilities: answering visitor questions grounded in
  the site corpus (reusing the existing grounded-answer path), and surfacing **downloadable
  resources** (PDFs/whitepapers already linked in Stratpoint's pages) when a visitor wants
  something to read. Deferred a lead-capture / callback capability to a later increment.
- Confirmed the cloud model (NVIDIA-hosted Gemma) supports native tool calling, which cleared
  the way to build the agent with LangChain's standard ReAct agent rather than a hand-written
  loop — chosen because it matches the course material and NVIDIA's own documented integration.
- Turned the spec into a step-by-step implementation plan
  (`docs/superpowers/plans/2026-07-05-agent-and-api-integration.md`) and built against it.
- Clarified the chatbot's prompt architecture: kept the agent's **tool-orchestration** prompt
  (which decides which tool to use) as a separate layer from the teammate-owned
  **grounded-answer** prompt engineering (how a cited answer is written). The two are already
  connected through the agent's search tool, so they were deliberately kept separate rather
  than merged into one.

**What we produced**
- A working ReAct customer-support agent and a `/chat` HTTP API that serves it, documented in
  the README (`README.md`, "Usage — Agent + API"). Verified end-to-end against the running
  service: the bot answers grounded questions and, on request, hands back a real downloadable
  report — e.g. surfacing the *WEF Future of Jobs 2023* PDF from Stratpoint's digital-maturity
  blog post.

**Open / to decide**
- Resource-finding reliability depends on how the question is phrased (the search matches
  source wording), so it can miss a document when the request is very terse. A **curated list
  of key downloadable resources** is the deferred, more dependable option — decide whether to add it.
- Lead capture / "have someone contact me" was deferred — decide whether it's in scope.
- The agent + API work sits on its own branch pending a decision on how to integrate it.

## 2026-07-04 — Verified retrieval; surfaced a model-sizing constraint

**What we did**
- Built a verification test suite for the RAG retrieval module (`RAG-UnitTests/`), covering
  both normal use and failure cases, to give the retrieval pipeline evidence-based reliability
  for the eval section of the write-up.
- Began connecting a language model so the system can turn retrieved Stratpoint content
  into written, grounded answers.
- Discovered the selected local model (`gemma4:e2b`) is too large to run on the intended
  deployment host (needs more memory than available, no GPU), making local answer generation
  impractically slow.
- Resolved that constraint by switching answer generation from the local model to a **cloud
  model endpoint** (NVIDIA-hosted Gemma), removing the deployment-host sizing limit.

**What we produced**
- A retrieval test suite (`RAG-UnitTests/`) confirming the search pipeline returns the right
  Stratpoint pages with their source links.
- A working cloud-backed answer path — the system now generates grounded answers without
  depending on local hardware.

**Open / to decide**
- Decision #1 (generation host) can now be closed in favor of the cloud endpoint — confirm
  with the group and update the plan/docs that still describe both routes.

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
