# General Log

Non-technical, report- and presentation-oriented record of what was done on this
project, **maintained by Claude Code via the `update-log` skill**.

This is raw material for a later report or presentation — milestones, decisions, and the
documents/artifacts produced. It deliberately leaves out technical mechanics (bug fixes,
branch merges, test runs). When the user asks to "update the log," the `update-log` skill
refreshes the newest entry below.

---

## 2026-07-09 — Added a user-controlled "reasoning" mode and let the bot show its thinking

**What we did**
- Established that the chatbot's existing "reasoning" was only a prompting trick (the model was
  asked to narrate its logic inside its answer, then that text was thrown away) — not the language
  model's own native thinking. Set out to switch to the model's real reasoning and let users see it.
- Brainstormed the feature and captured the agreed design in a spec
  (`docs/superpowers/specs/2026-07-08-native-reasoning-toggle-design.md`), then turned it into a
  step-by-step implementation plan (`docs/superpowers/plans/2026-07-08-native-reasoning-toggle.md`)
  and built against it.
- Ran a short live experiment against the hosted model to confirm how its reasoning actually behaves
  before committing to the design (see decisions below).

**Key decisions**
- **Make reasoning a user choice, as an on/off "speed" switch.** Added an *Enable reasoning* toggle to
  the chat interface: off gives faster, direct answers; on lets the model think step-by-step for more
  thorough answers. Applies to both the plain-question path and the document-lookup path.
- **Replace the old "pretend" reasoning with the model's real thinking.** Removed the discarded
  narrated-reasoning field and its supporting prompt scaffolding, letting the model's native reasoning
  take over that role.
- **Surface the reasoning to the user.** The model's thinking now appears in the "under the hood"
  debug panel and in the raw response, so it can be inspected or shown in a demo.
- **A live finding shaped the design:** the model only returns its reasoning when *not* forced into
  strict structured-output mode, so the reasoning path deliberately relaxes that constraint on the
  plain-question path while keeping answers clean.
- **Stop over-asking users to clarify.** A specific request — e.g. *"Do you have a document for
  Stratpoint's quality assurance?"* — was being met with "What would you like to know?" instead of the
  document, because the topic wasn't on a hardcoded list. Decided that clear questions and explicit
  document requests should always go straight to an answer; only genuinely vague input asks for
  clarification.

**What we produced**
- A user-facing reasoning toggle end-to-end (chat UI → service → both answer paths), verified live:
  reasoning shows when enabled and is absent when off.
- A fix that makes document requests return the actual file — the quality-assurance query now returns
  Stratpoint's QA one-pager PDF instead of a clarification prompt.

**Open / to decide**
- The reasoning toggle was verified through the service directly; a final click-through of the chat UI
  itself is still pending.
- The chatbot recognizes topics from a fixed hardcoded list, which is inherently brittle; worth
  revisiting if we want richer topic-aware routing later.

## 2026-07-08 — Hardened chatbot reliability: stopped good answers being wrongly safety-blocked, and recovered documents that existed but couldn't be found

**What we did**
- Investigated a failure where the bot replied *"I generated a response, but it failed safety
  checks"* and refused document/resource requests **even though it had actually produced a correct,
  well-sourced answer**. Traced it to the resource-handling path not passing its own sources to the
  safety checks, so the grounding check had nothing to verify against and discarded the answer.
- Investigated a second failure where the bot said a document wasn't available **when it existed in
  the site corpus** — reproduced with a World Bank report on "mobile phone usage patterns" that the
  bot repeatedly failed to surface — and traced it to two separate weaknesses in how the site is
  searched and split into passages.

**Key decisions**
- **Verify answers against the bot's own sources, and never discard an answer just because there's
  nothing to check.** The safety layer now treats "no sources to verify against" as "can't verify"
  (allow, and flag) rather than an automatic failure — so a correct, grounded answer is no longer
  thrown away on document/resource requests.
- **Retune the site search for high recall.** Diagnosis showed the search was silently missing the
  single most-relevant passage for many questions; the search index was rebuilt with settings that
  reliably surface the best passage.
- **Never split a document link across a passage boundary.** Links to downloadable reports were
  sometimes cut in half when a page was chunked, making them unusable; passage-splitting now keeps
  each link intact.

**What we produced**
- A more reliable chatbot: document/resource requests now return their grounded answer instead of a
  safety refusal, and the answer's sources and grounding status also flow through to the
  "under the hood" debug panel on that path.
- A verified retrieval recovery: the previously-missing World Bank "mobile phone usage patterns"
  report now surfaces on request. The search index was **rebuilt once** with the improved settings,
  so it is demo-ready.
- Expanded the retrieval "answer-quality" regression set with this newly-fixed case, building on the
  seed started on 2026-07-07.

**Open / to decide**
- Still unresolved from 2026-07-07: whether the ReAct agent should handle **all** questions or only
  resource requests — the two reliability fixes touch that path but didn't settle the question.
- These reliability fixes plus the earlier guardrail-integration work still need to be folded into
  the main line of work — decide how and when to integrate.
- A fuller "answer-quality" evaluation set (now seeded with several fixed cases) would catch future
  search regressions automatically — decide whether to build it out.

## 2026-07-07 — Connected the chatbot's parts, added in-chat file downloads, and fixed a corpus-retrieval gap

**What we did**
- Audited how the separately-built pieces of the chatbot fit together (chat UI, disambiguation,
  and guardrails built by teammates; the ReAct agent and API are the owned parts), and connected
  the agent's sources, reasoning trace, and grounding/refusal status through to the chat UI's
  "under the hood" debug panel — which had been designed to display that information but wasn't
  actually receiving it.
- Added a new capability: when the bot surfaces a downloadable resource (a PDF or whitepaper),
  the visitor can now **download the file directly in the chat**, not just follow a link.
- Diagnosed and fixed a retrieval-quality gap: the bot was replying that information wasn't in
  its knowledge base even when the exact text existed in the site corpus — reproduced with a
  Nucleus Research cloud-adoption statistic and a World Economic Forum infrastructure reference,
  both of which the bot failed to find.

**Key decisions**
- **Smaller retrieval chunks (the fix).** Root cause: each page was split into large chunks, so a
  single valuable sentence was diluted among the surrounding text and ranked too low to be
  retrieved. Halved the chunk size so individual facts stand out, and correspondingly retrieve
  more chunks per question to keep each answer's supporting context full. This also directly
  improves the "terse questions miss a document" reliability concern raised in the 2026-07-05
  agent entry.
- **In-chat downloads fetched app-side, with the top result ready instantly.** The suggested file
  downloads immediately; any additional files download on click. Because the linked files live on
  third-party sites, the download path validates each target before fetching to avoid pulling from
  unsafe or internal addresses.

**What we produced**
- A more fully connected chatbot: the chat UI now shows the agent's sources, reasoning trace, and
  grounding/guardrail status that the interface was built to display.
- An in-chat file-download feature for downloadable resources.
- A verified retrieval fix: the two example questions that previously failed now return the
  correct page and hand back the right downloadable report (the Nucleus cloud-adoption PDF and the
  WEF Global Risks Report).
- A handoff document capturing this session's state for the next contributor
  (`docs/handoff-rag-integration-and-retrieval-fix.md`).

**Open / to decide**
- The retrieval fix requires **rebuilding the search index once** before a demo (the page contents
  didn't change, only how they're split, so a normal rebuild skips the work) — boot it in advance
  or it keeps the old, coarser splitting.
- Decide whether the ReAct agent should handle **all** questions or only resource requests —
  currently simple questions bypass it and only resource-style requests go through the full agent.
- The in-chat download feature has been checked in isolation but **not yet exercised in the fully
  running app** — verify it live before relying on it in a demo.
- Growing a small retrieval "answer-quality" test set (seeded with the two now-fixed cases) would
  catch future retrieval regressions — decide whether to build it out.

## 2026-07-07 — Built, ran, and verified the Dockerization module

**What we did**
- Reviewed the now-assembled app to ground the packaging work in what actually runs, and
  wrote a focused planning document for the Dockerization module
  (`docs/plan-dockerization.md`). It supersedes the dockerization section of the earlier
  combined plan (`docs/plan-rag-dockerization.md` §4), which was written before the app was
  built and left key choices open.
- Confirmed the deployment picture had simplified since that earlier plan: because answer
  generation now runs on a **cloud model endpoint** (NVIDIA-hosted Gemma) rather than a local
  model, the container setup needs **no model container and no GPU** — a smaller, more reliable
  build. Embeddings still run locally inside the image.
- Chose a **two-service, one-image** layout: the same image runs both the API and the chat UI,
  with the UI reaching the API over the internal network. This maps directly onto the
  assignment's "accessible via a web UI *and* an API endpoint" requirement while keeping a
  single build to maintain.
- Ran a quality-assurance pass over the container setup, then **built and ran the whole app in
  Docker end-to-end for the first time** — a single `docker compose up` command brought both the
  API and the chat UI online and answered questions. This satisfies the assignment's core
  requirement that the app "builds and runs cleanly with a single command."
- Confirmed the LLMOps monitoring module is no longer part of the project, so it was dropped
  from the container design entirely.

**Key decisions**
- **Run on a single machine via Docker**: the team will demo by running the containerized app on
  one computer, so the run instructions were simplified to a single `docker compose up` and the
  earlier notes about alternative host setups were removed.
- **Cloud model → no local model container**: the earlier plan's interim local-model option is
  retired; the packaged app calls the cloud endpoint, removing the GPU/host-sizing constraint.
- **Corpus stays outside the image**: the finished site corpus is mounted read-only at run time
  and the web crawler is kept out of the image, since the app only reads the corpus.
- **Vectors built on first start and reused**: the app embeds the corpus the first time it runs
  and persists it, so later starts are fast.

**What we produced**
- Planning document: `docs/plan-dockerization.md` (decisions, build order, run instructions,
  and presentation talking points), plus a pointer added to the superseded section of
  `docs/plan-rag-dockerization.md`.
- A complete, **verified-working** container setup — `Dockerfile`, `docker-compose.yml`, startup
  script (`docker/entrypoint.sh`), and build-context exclusions (`.dockerignore`) — that runs the
  whole chatbot (API + UI) with a single `docker compose up --build`, confirmed serving live.
- Documentation: a "Usage — Docker" section in the `README.md` with prerequisites and the
  single-command run, and the environment template (`.envexample`) updated so a fresh clone has
  every setting it needs.

**Open / to decide**
- The very first container start is a one-time slow step (it downloads the embedding stack and
  embeds all 371 pages). The vectors then persist between runs, so before a live demo the app
  should be booted once in advance so the graded run starts instantly.

## 2026-07-05 — Completed guardrails, made NeMo the default backend, fixed false-positive advice blocker

**What we did**
- Analyzed the existing chatbot pipeline (ReAct agent, API, UI built by teammates) and designed guardrails and disambiguation as non-invasive middleware layers that wrap the agent rather than replacing it — preserving the existing `AgentResult` contract the UI depends on.
- Built the **guardrails module** (`src/stratpoint_rag/guardrails/`) with an input pipeline (PII redaction, keyword blocking, topic filtering) and an output pipeline (hallucination detection via embedding cosine similarity, PII leak cross-referencing against source documents, and advice blocking for medical/legal/financial content). All checks are heuristic-first; the optional LLM fallback is only invoked when heuristics are inconclusive.
- Built the **disambiguation module** (`src/stratpoint_rag/disambiguation/`) with a heuristic-first intent classifier (greeting/harmful/off-topic/Stratpoint/needs-clarification), regex-based slot extraction for known entities (OutSystems, Flutter, AWS, projects), and a multi-turn clarification loop (max 3 turns) that asks natural follow-up questions when slots are missing.
- Integrated **NeMo Guardrails** as the default backend (`src/stratpoint_rag/guardrails/nemo/`) with Colang 2.x flows that wire all five custom actions (PII redaction, topic relevance, output PII check, hallucination check, advice blocking) alongside NeMo's built-in library rails — providing the same guardrail coverage as the built-in pipeline. Falls back gracefully to the built-in pipeline when `nemoguardrails` is not installed.
- Created a **wrapper function** (`agent/guardrail_agent.py:run_with_guardrails`) that runs input guardrails → disambiguation → answer (fast path: direct LLM call for simple Q&A, ReAct agent only for resource requests) → output guardrails → memory update, then updated the API endpoint to call it. The return type (`AgentResult`) is unchanged, so the Streamlit UI works without modifications.
- Documented the full architecture (`docs/architecture-flow.md`) with an ASCII flow diagram, module map, guardrails deep-dive, disambiguation deep-dive, and decision log — designed as panel-defense material.

**Key decisions**
- **Source-aware advice blocker**: Advice patterns are now directive-only (e.g., `` "you should see a doctor" `` instead of raw keyword matching on "diagnosis" or "treatment") and cross-reference against retrieved source chunks — avoiding false positives when Stratpoint's own content mentions healthcare or financial services.
- **Fast path for simple Q&A**: Queries without resource keywords (PDF/whitepaper/download) bypass the 3-LLM-call ReAct agent and go through a single `rag.answer()` call, cutting typical response time from 60–120s to 15–30s.
- **NeMo as default toggle**: NeMo Guardrails became the main backend — `use_nemo=True` is now the default on the API, with automatic fallback when the optional dependency is not installed.

**What we produced**
- Guardrails module: input pipeline (PII redaction, keyword blocking, topic filter), output pipeline (PII checker, hallucination checker, advice blocker), conversation memory, composable pipeline orchestrator (`guardrails/pipeline.py`)
- Disambiguation module: intent classifier, slot extractor, clarification loop, router (`disambiguation/`)
- NeMo backend: config, Colang flows (with wired custom actions), topic-bassed disallowed rails, wrapper (`guardrails/nemo/`, `guardrails/nemo_guardrails.py`)
- Integration wrapper: `agent/guardrail_agent.py`
- Documentation: `docs/architecture-flow.md`, `docs/MIKHOS_self-log.md`
- 55 tests covering guardrails, disambiguation, the wrapper, and both NeMo and built-in paths — all offline, no network required
- All 128 tests pass

**Open / to decide**
- The Nemmo optional dependencies (`nemoguardrails`, `langchain-community`) are listed in `[project.optional-dependencies] nemo` — install with `uv sync --extra nemo` for the full NeMo backend.
- The hallucination checker uses bge-small embeddings by default (cosine similarity threshold 0.75) — tune this threshold if eval shows false positives.

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
