# Architecture — `stratpoint_rag`

The chatbot half of the repository. `stratpoint_rag` is organized as one subpackage per pipeline concern: `rag` (corpus → chunks → embeddings → Chroma → `retrieve()` → grounded answer), `prompts` (the system-prompt variants and the `GroundedAnswer` contract), `disambiguation` (intent classification, slot extraction, clarification loop), `guardrails` (input/output safety, plus an optional NeMo layer), `agent` (the ReAct loop, its tool contracts, and the guarded orchestrator that ties everything together), `llmops` (request telemetry), `api` (FastAPI), and `ui` (Streamlit). Dependencies flow inward toward `rag`: everything may import `rag`, `rag` imports only `prompts` and `llmops`, `llmops` imports nothing first-party, and `prompts` never imports `rag` at runtime (only under `TYPE_CHECKING`).

**Scope**: this document covers `src/stratpoint_rag/` only. The sibling `stratpoint_crawl` package is out of scope — it is treated as a stable upstream whose only interface is the on-disk corpus (`data/pages/*.md` + `data/index.jsonl`). `stratpoint_rag` never imports it.

**Related**: `docs/architecture-flow.md` narrates the same system as a runtime request flow with deep-dives on guardrail and disambiguation policy. This document is the file-level map — what each file is and what it depends on.

## Directory overview

| Directory | Role |
|---|---|
| `rag/` | Retrieval core: corpus loading, chunking, embeddings, Chroma persistence, the `retrieve()` seam, the grounded-answer LLM call, and the ingest CLI |
| `rag/eval/` | Lightweight retrieval eval (`hit@k`) over a small gold question set |
| `prompts/` | System-prompt variants V0–V4, few-shot examples, `GroundedAnswer` schema, `build_prompt()` seam, and the ablation runner |
| `disambiguation/` | Intent classification, slot extraction, and the clarification loop that runs before retrieval |
| `guardrails/` | Input/output checks: keyword blocking, PII redaction, topic filtering, hallucination detection, advice blocking, conversation memory |
| `guardrails/nemo/` | Optional NeMo Guardrails config: Colang 2.x flows plus custom actions that delegate back to the built-in checks |
| `agent/` | Plain-text ReAct agent, its five tools and their typed contracts, the pluggable tracer, and `run_with_guardrails()` — the single orchestration entry the API calls |
| `llmops/` | Request telemetry: a JSONL trace sink, a thread-local token accumulator, and stdlib aggregation (latency p50/p95, tokens, error rate) |
| `api/` | FastAPI app exposing `POST /chat`, `GET /health`, and `GET /metrics` |
| `ui/` | Streamlit chat UI: transcript, debug panel, resource downloads, API client |
| `evaluation/` | Results and findings artifacts for the prompt-engineering experiment (no code) |

## Entry points

| Command | What it runs |
|---|---|
| `uv run stratpoint-rag-ingest` | `rag/ingest.py:main` — embeds the corpus into Chroma, `content_hash`-gated. Add `--force` to re-embed everything, `--data-dir` to point elsewhere. Must run before any query. |
| `uv run uvicorn stratpoint_rag.api.app:app --port 8000` | `api/app.py` — the HTTP API |
| `uv run streamlit run src/stratpoint_rag/ui/app.py` | `ui/app.py` — the chat UI (expects the API at `STRATPOINT_API_URL`, default `http://localhost:8000`) |
| `uv run python -m stratpoint_rag.rag.eval.run` | `rag/eval/run.py` — prints `hit@k` over `gold.jsonl`. Needs a populated Chroma store. |
| `uv run python -m stratpoint_rag.prompts.run_ablation` | `prompts/run_ablation.py` — runs the prompt variants over the fixed test set, writing `evaluation/prompt_ablation_results.jsonl`. Needs a populated store and `NVIDIA_API_KEY`. |
| `python website_calculator.py` | Repo-root standalone script (stdlib only, not part of either package and not imported by the agent) — an interactive/CLI website-design cost estimator. Reference pricing logic for the `estimate_cost_and_timeline` tool, which is still a stub. |

The repo-root `run.ps1` / `run.bat` / `run.sh` launchers start the API and UI together; `docker-compose.yml` runs the same pair in containers.

## Data flow

Two independent paths meet at the Chroma store.

**Ingestion (offline, idempotent)**

1. `rag/loader.py` reads `data/index.jsonl`, keeps rows whose `status` is `ok` or `skipped` (the corpus invariant), and loads each matching `data/pages/<slug>.md`, stripping YAML frontmatter. A missing `.md` is warned and skipped rather than fatal.
2. `rag/chunker.py` splits each page body into ~800-char overlapping chunks, snapping split boundaries out of markdown link spans so no link is ever cut in half.
3. `rag/embeddings.py` embeds the chunk texts with `bge-small-en-v1.5` via sentence-transformers, normalized for cosine similarity.
4. `rag/store.py` upserts them into a persistent Chroma collection with high-recall HNSW settings, stamping each chunk's metadata with the page's `content_hash`.
5. `rag/ingest.py` drives all of the above, re-embedding only pages whose `content_hash` changed and deleting slugs that dropped out of the manifest.

**Query (per request)**

1. `api/app.py` receives `POST /chat`, resets the `llmops` token accumulator for the request, and calls `agent/guardrail_agent.py:run_with_guardrails()`.
2. Input guardrails: built-in `KeywordBlocker` then `PIIRedactor` (fast, no API cost), then optionally the NeMo input rails.
3. `disambiguation/router.py` classifies intent and extracts slots; greetings, off-topic, harmful, and too-vague inputs are answered without retrieval.
4. Answer, on one of two branches:
   - resource-shaped requests → `agent/agent.py:run_agent()`, a plain-text ReAct loop over the five tools, with each tool's retrieved chunks (and any assembled proposal data) captured through context vars so the output guardrails have something to verify against;
   - everything else → `rag/answer.py:answer_grounded()`, a single call: `retrieve(query, k=8)` → `prompts/builder.py:build_prompt(..., "v4_combined_lowtemp")` → NVIDIA NIM → `GroundedAnswer` validation.

   Either way retrieval begins with `rag/query_rewrite.py:anchor_entity()`, which swaps second-person pronouns for "Stratpoint" before embedding so the query carries an entity token.
5. Output guardrails: built-in `AdviceBlocker` → `HallucinationChecker` (embedding cosine similarity ≥ 0.6 against the source chunks) → `OutputPIIChecker`, then optionally the NeMo output rails.
6. `guardrails/memory.py` records the turn and tracks the clarify streak that drives the escalation hand-off; the `AgentResult` returns to the UI, whose debug panel renders citations, trace, reasoning, and grounding status.
7. Back at the API boundary, `_record()` pops the accumulated token usage and appends one `llmops` trace line (latency, model, tokens, tool calls, grounding, error) — on the success *and* the error paths. Query text is deliberately never written.

## File reference

### `rag/`

| File | What it does | Depends on |
|---|---|---|
| `rag/config.py` | Env-switched settings read at call time (Chroma path/collection, embedding provider/model, NIM model/base-URL/key/timeout); calls `load_dotenv()` on import | — |
| `rag/models.py` | `Chunk` — a retrievable page slice carrying its source URL, title, and retrieval score | — |
| `rag/loader.py` | Loads the crawled corpus off disk, honoring the `ok`/`skipped` corpus invariant; strips frontmatter | `data/index.jsonl`, `data/pages/*.md` |
| `rag/chunker.py` | Splits page markdown into overlapping chunks without ever cutting a markdown link | `rag/models.py` |
| `rag/embeddings.py` | `Embedder` protocol plus the local sentence-transformers implementation; the swap seam for a future cloud embedder | `rag/config.py` |
| `rag/store.py` | Chroma persistence: upsert-by-page, `content_hash` bookkeeping, slug deletion, and cosine-similarity query | `rag/config.py`, `rag/models.py` |
| `rag/ingest.py` | The ingest CLI — `content_hash`-gated embedding of the whole corpus, plus eviction of dropped pages | `rag/chunker.py`, `rag/embeddings.py`, `rag/loader.py`, `rag/store.py` |
| `rag/query_rewrite.py` | `anchor_entity()` — a pure, deterministic rewrite replacing second-person/first-person-plural pronouns with "Stratpoint" so pronoun-only questions carry an entity token into the embedding. Skips queries that already name the company or run past ~20 words (pasted source text, e.g. `find_resource`'s near-verbatim lookups) | — |
| `rag/retrieve.py` | `retrieve(query, k)` — the public retrieval seam; anchors the query, then lazily builds one embedder and store per process, injectable for tests | `rag/embeddings.py`, `rag/store.py`, `rag/models.py`, `rag/query_rewrite.py` |
| `rag/answer.py` | The production answer path: retrieve → build the V4 prompt → call NIM → validate `GroundedAnswer`, falling back to the raw reply on parse failure. Also handles NIM's reasoning mode and markdown-fenced JSON, and reports its token usage to `llmops` | `rag/config.py`, `rag/retrieve.py`, `rag/models.py`, `prompts/builder.py`, `prompts/schema.py`, `llmops/` |
| `rag/eval/run.py` | Prints `hit@k` — whether the expected source page appears among the top-k chunks for each gold question | `rag/retrieve.py`, `rag/eval/gold.jsonl` |
| `rag/eval/gold.jsonl` | Five seed questions paired with the slug that should be retrieved | — |

### `prompts/`

| File | What it does | Depends on |
|---|---|---|
| `prompts/schema.py` | `GroundedAnswer` and `Citation` — the structured-JSON contract the LLM must return (`answer`, `citations`, `is_grounded`, `confidence`) | — |
| `prompts/few_shot_examples.py` | Curated few-shot examples in both JSON and free-text form (grounded, partially grounded, and refusal cases) | — |
| `prompts/system_prompts.py` | The five system-prompt templates V0–V4; V2–V4 carry a `{schema_format}` placeholder the builder fills with the JSON schema | `prompts/few_shot_examples.py` |
| `prompts/builder.py` | `build_prompt(query, chunks, variant) -> (system, user)`. The user prompt is held byte-identical across variants so the system prompt is the sole independent variable | `prompts/schema.py`, `prompts/system_prompts.py`, `rag/models.py` (`TYPE_CHECKING` only) |
| `prompts/registry.py` | `PROMPT_VARIANTS` — the seven named variants (V0–V4 plus `v4_combined_hightemp` and `v4_combined_reasoning`) pinning `use_schema`, `temperature`, and `top_p` per experiment | — |
| `prompts/run_ablation.py` | Runs every variant across seven fixed questions (5 answerable + 2 out-of-scope) with retrieval held constant at `k=3`, scoring JSON validity, refusal correctness, and mean confidence into `evaluation/prompt_ablation_results.jsonl` | `rag/config.py`, `rag/retrieve.py`, `prompts/builder.py`, `prompts/registry.py`, `prompts/schema.py` |

### `disambiguation/`

| File | What it does | Depends on |
|---|---|---|
| `disambiguation/schemas.py` | The module's Pydantic types: `IntentCategory`, `IntentQuery`, `SlotDef`/`SlotQuery`, `ClarificationTurn`/`ClarificationSession`, `RouteResult` | — |
| `disambiguation/classifier.py` | Heuristic-first intent classification (greeting / harmful / off-topic / Stratpoint / vague), falling back to an LLM classifier only when confidence < 0.7 and an API key exists | `rag/config.py`, `disambiguation/schemas.py` |
| `disambiguation/slots.py` | Regex slot extraction for `topic`, `service_type`, and `project_name`, and the per-intent required-slot table; also returns the matched keyword for downstream retrieval tuning | `disambiguation/schemas.py` |
| `disambiguation/clarification.py` | The bounded (max 3 turn) clarification loop: which question to ask next, how to fold an answer back into confirmed slots, and serialization to/from a dict | `disambiguation/schemas.py`, `disambiguation/slots.py` |
| `disambiguation/router.py` | Turns a classified intent into a `RouteResult`: canned replies for greeting/off-topic/harmful, a clarification question when the input is genuinely vague, otherwise `should_retrieve=True`. Skips demotion for structurally specific asks (questions, resource requests) | `disambiguation/classifier.py`, `disambiguation/clarification.py`, `disambiguation/slots.py`, `disambiguation/schemas.py`, `guardrails/memory.py` |

### `guardrails/`

| File | What it does | Depends on |
|---|---|---|
| `guardrails/schemas.py` | `GuardrailResult` (passed / action / message / modified text), `RedactionRule`, and `GuardrailConfig` (fail-open vs fail-closed, per-check toggles) | — |
| `guardrails/memory.py` | `ConversationMemory` — a rolling 6-turn buffer per session plus the `clarify_streak` counter driving escalation | — |
| `guardrails/input_guardrails.py` | `PIIRedactor` (SSN / card / email / phone, with an allowed-domain exemption), `TopicFilter` (keyword match, advisory LLM fallback), `KeywordBlocker` (injection, jailbreak, harmful, attack patterns), and the `InputPipeline` that sequences them | `rag/config.py`, `guardrails/schemas.py` |
| `guardrails/output_guardrails.py` | `OutputPIIChecker` (redacts only PII absent from the sources), `HallucinationChecker` (cosine similarity ≥ 0.6, optional LLM judge), `AdviceBlocker` (directive-only, source-aware medical/legal/financial patterns), and the `OutputPipeline` | `rag/config.py`, `rag/embeddings.py`, `rag/models.py`, `guardrails/input_guardrails.py`, `guardrails/schemas.py` |
| `guardrails/pipeline.py` | `GuardrailPipeline` — composes the input and output pipelines behind `run_input()` / `run_output()`, honoring the config toggles | `guardrails/input_guardrails.py`, `guardrails/output_guardrails.py`, `guardrails/schemas.py`, `rag/models.py` |
| `guardrails/nemo_guardrails.py` | Wraps NeMo `LLMRails` behind the same two-method interface, pointing its model at `rag.config.llm_model()`. Detects a fired rail by the extra assistant message NeMo appends, not only by exceptions; raises `ImportError` when `nemoguardrails` is absent so callers can degrade | `rag/config.py`, `rag/models.py`, `guardrails/schemas.py`, `guardrails/nemo/` |
| `guardrails/nemo/__init__.py` | Imports `actions` so the custom actions register with NeMo | `guardrails/nemo/actions.py` |
| `guardrails/nemo/actions.py` | Five custom Colang actions that delegate straight back to the built-in components — PII redaction, topic relevance, output PII, hallucination, advice | `guardrails/input_guardrails.py`, `guardrails/output_guardrails.py`, `rag/models.py` |
| `guardrails/nemo/config.yml` | NeMo model config (NIM endpoint via `NVIDIA_API_KEY`), general instructions, `colang_version: 2.x` | — |
| `guardrails/nemo/main.co` | The input and output rail flows wiring the custom actions alongside NeMo's library rails (self-check input, jailbreak heuristics) | `guardrails/nemo/actions.py` |
| `guardrails/nemo/rails/disallowed.co` | Canonical-form flows refusing illegal-activity, medical, legal, and financial-advice asks | — |

### `agent/`

| File | What it does | Depends on |
|---|---|---|
| `agent/models.py` | `AgentResult` / `Step` / `Link` / `ProposalData` — the structured result types, in their own module so `react.py` and `agent.py` don't import each other | `agent/contracts.py` |
| `agent/contracts.py` | The typed Pydantic input/output contracts for the three proposal tools (`BriefParserInput`/`ExtractedRequirements`, `EstimationInput`/`EstimationResult`, `ProposalPDFInput`/`PDFGenerationResult`). These are the interface teammates implement against so the loop and API stay unchanged | — |
| `agent/tracer.py` | `AgentTracer` ABC plus `NoOpTracer` (the default) and `ConsoleTracer`, with module-level `get_default_tracer()` / `set_default_tracer()`. The hook that lets an observability SDK be injected without the loop depending on one | — |
| `agent/react.py` | The plain-text ReAct loop: renders the system prompt from `TOOL_SPECS`, calls NIM over `httpx` with `stop=["Observation:", "PAUSE"]`, parses `Thought`/`Action`/`Answer`, dispatches tools with one retry (surfacing the error back to the model as an observation rather than crashing), emits tracer events, and falls back to `search_stratpoint` when the loop can't finish | `rag/config.py`, `agent/tools.py`, `agent/models.py`, `agent/tracer.py`, `llmops/` |
| `agent/agent.py` | `run_agent()` — the public seam, delegating to `react.run_react`; re-exports the models | `agent/react.py`, `agent/models.py` |
| `agent/tools.py` | The five tools the agent may call: `search_stratpoint` (grounded Q&A) and `find_resource` (PDF links mined from retrieved chunks), both live, plus the proposal trio `parse_client_brief` / `estimate_cost_and_timeline` / `generate_proposal_pdf`, which are **typed stubs** carrying `# TODO(teammate - …)` markers. String-in/string-out wrappers adapt the typed tools to the loop's plain-text calling convention. Also holds `TOOL_SPECS`/`TOOL_REGISTRY` (whose descriptions render into the system prompt) and the context-var capture sinks that let the guardrail layer read back what the tools grounded on | `rag/answer.py`, `rag/retrieve.py`, `agent/contracts.py`, `agent/models.py` |
| `agent/guardrail_agent.py` | `run_with_guardrails()` — the orchestrator: input guardrails → disambiguation → answer (ReAct branch for resource asks, direct RAG otherwise, with a Chroma `$contains` augmentation for contact/location queries) → output guardrails → memory and escalation. Also `clear_memory()` | `agent/agent.py`, `agent/tools.py`, `disambiguation/router.py`, `disambiguation/schemas.py`, `guardrails/memory.py`, `guardrails/pipeline.py`, `guardrails/schemas.py`, `rag/answer.py`, `rag/store.py` |
| `agent/__init__.py` | Re-exports the public seam: `run_agent`, `run_with_guardrails`, `clear_memory`, the result models, the tracer classes, and the tool contracts | `agent/agent.py`, `agent/guardrail_agent.py`, `agent/contracts.py`, `agent/tracer.py` |
| `agent/README.md` | Package-level note: why a hand-rolled ReAct loop over LangChain/LangGraph, the tool contracts table, and how a teammate swaps a stub for a real implementation | `agent/contracts.py`, `agent/tools.py` |

### `llmops/`

| File | What it does | Depends on |
|---|---|---|
| `llmops/__init__.py` | `record()` — stamps a UTC timestamp and writes one telemetry row (latency, model, tokens, tool calls, grounding, error) through the sink; re-exports the package's public surface | `llmops/sink.py`, `llmops/metrics.py`, `llmops/usage.py` |
| `llmops/sink.py` | The JSONL trace sink: append-under-a-lock and read-back, path from `LLMOPS_LOG_PATH`, disabled by `LLMOPS_ENABLED=0`. No new dependency, no port, no account — it works offline on the LXC | — |
| `llmops/usage.py` | A thread-local token accumulator. One turn makes several NIM calls (the ReAct loop, plus a nested RAG call inside `search_stratpoint`), so usage can't be read off a single response — every call site adds, the request boundary resets and pops | — |
| `llmops/metrics.py` | `aggregate()` — count, latency p50/p95 (linear-interpolated percentile), total/avg tokens, and error rate over a list of records. Pure stdlib, no numpy | — |

### `api/`

| File | What it does | Depends on |
|---|---|---|
| `api/app.py` | FastAPI app: `GET /health`; `POST /chat` taking `{message, history, session_id, use_nemo, enable_reasoning}` and returning `AgentResult`, mapping config errors to 503 and upstream LLM failures to 502 without leaking details; `GET /metrics` returning the `llmops` aggregates plus the 50 most recent records, newest first. Records telemetry on both the success and error paths | `agent/`, `llmops/`, `rag/config.py` |
| `api/__init__.py` | Re-exports `app` | `api/app.py` |

### `ui/`

| File | What it does | Depends on |
|---|---|---|
| `ui/app.py` | The Streamlit entry point: page config, sidebar (API health, session ID, reset, reasoning toggle), transcript, and the chat-input round trip | `ui/state.py`, `ui/api_client.py`, `ui/components/*` |
| `ui/api_client.py` | Thin HTTP client for the API (`health_check`, `send_message`) with typed `APIError` messages for connection, timeout, and HTTP failures | — |
| `ui/state.py` | Streamlit session-state init and conversation reset (messages plus a UUID session ID) | — |
| `ui/resource_fetch.py` | Deliberately Streamlit-free server-side fetching of externally hosted resource files: rejects non-public hosts (SSRF guard), caps the body size, and never raises so callers can fall back to the plain link | — |
| `ui/components/chat_transcript.py` | Replays the stored transcript, re-rendering downloads and the debug panel for each assistant turn under a stable per-message key | `ui/components/debug_panel.py`, `ui/components/resource_downloads.py` |
| `ui/components/debug_panel.py` | The "Under the hood" expander: sources, agent trace, native reasoning, grounding/guardrail status, and the raw JSON | — |
| `ui/components/resource_downloads.py` | Download buttons for `find_resource` results — top result fetched eagerly, the rest on click, cached for an hour, falling back to an external link when a fetch is refused | `ui/resource_fetch.py` |
| `ui/.streamlit/config.toml` | Theme (light base, Stratpoint blue accent, Poppins) | — |
| `ui/README.md` | How to run the UI standalone and point it at a non-local API | — |

### `evaluation/`

| File | What it does | Depends on |
|---|---|---|
| `evaluation/PROMPT_ENGINEERING_FINDINGS.md` | Write-up of the prompt-variant experiment: what each variant tests, the comparative metrics, and why `v4_combined_lowtemp` won | `evaluation/prompt_ablation_results.jsonl` |
| `evaluation/prompt_ablation_results.jsonl` | Per-variant, per-question raw output from `prompts/run_ablation.py` | — |
| `evaluation/__init__.py` | Placeholder for the planned retrieval / answer-quality eval module | — |

## Dependency diagram

First-party relationships only; `config.py` and the various `schemas.py` leaves are omitted where they would only add edges.

```mermaid
graph TD
    subgraph corpus["data/ (produced by stratpoint_crawl)"]
        INDEX["index.jsonl + pages/*.md"]
    end

    subgraph ragpkg["rag/"]
        LOADER[loader.py] --> CHUNKER[chunker.py]
        CHUNKER --> INGEST[ingest.py]
        EMB[embeddings.py] --> INGEST
        STORE[store.py] --> INGEST
        EMB --> RETRIEVE[retrieve.py]
        STORE --> RETRIEVE
        QRW[query_rewrite.py] --> RETRIEVE
        RETRIEVE --> ANSWER[answer.py]
        RETRIEVE --> EVAL[eval/run.py]
    end

    subgraph promptspkg["prompts/"]
        FEWSHOT[few_shot_examples.py] --> SYSP[system_prompts.py]
        SYSP --> BUILDER[builder.py]
        SCHEMA[schema.py] --> BUILDER
        BUILDER --> ABL[run_ablation.py]
        REG[registry.py] --> ABL
    end

    subgraph disambpkg["disambiguation/"]
        SLOTS[slots.py] --> CLARIFY[clarification.py]
        CLS[classifier.py] --> ROUTER[router.py]
        CLARIFY --> ROUTER
        SLOTS --> ROUTER
    end

    subgraph grpkg["guardrails/"]
        INGR[input_guardrails.py] --> OUTGR[output_guardrails.py]
        INGR --> GPIPE[pipeline.py]
        OUTGR --> GPIPE
        INGR --> NACT[nemo/actions.py]
        OUTGR --> NACT
        NACT --> NEMO[nemo_guardrails.py]
        MEM[memory.py]
    end

    subgraph agentpkg["agent/"]
        CONTRACTS[contracts.py] --> TOOLS[tools.py]
        CONTRACTS --> AMODELS[models.py]
        TOOLS --> REACT[react.py]
        TRACER[tracer.py] --> REACT
        AMODELS --> REACT
        REACT --> AGENT[agent.py]
        AGENT --> GAGENT[guardrail_agent.py]
        TOOLS --> GAGENT
    end

    subgraph opspkg["llmops/"]
        SINK[sink.py] --> OPS[__init__.py]
        USAGE[usage.py] --> OPS
        METRICS[metrics.py] --> OPS
    end

    subgraph uipkg["ui/"]
        RFETCH[resource_fetch.py] --> RDL[components/resource_downloads.py]
        DBG[components/debug_panel.py] --> TRANS[components/chat_transcript.py]
        RDL --> TRANS
        TRANS --> UIAPP[app.py]
        APICLIENT[api_client.py] --> UIAPP
        UISTATE[state.py] --> UIAPP
    end

    INDEX --> LOADER
    INGEST -.->|writes| CHROMA[(chroma_db/)]
    CHROMA -.->|reads| RETRIEVE
    BUILDER --> ANSWER
    SCHEMA --> ANSWER
    ANSWER --> TOOLS
    RETRIEVE --> TOOLS
    ANSWER --> GAGENT
    STORE --> GAGENT
    ROUTER --> GAGENT
    GPIPE --> GAGENT
    MEM --> GAGENT
    MEM --> ROUTER
    NEMO -.->|optional| GAGENT
    EMB --> OUTGR
    GAGENT --> APIAPP["api/app.py"]
    APIAPP -.->|HTTP| APICLIENT
    ABL -.->|writes| ABLRES[(evaluation/prompt_ablation_results.jsonl)]
    USAGE -.->|add_usage| ANSWER
    USAGE -.->|add_usage| REACT
    OPS --> APIAPP
    SINK -.->|writes| TRACES[(llmops_traces.jsonl)]
```

## Data artifacts

| Artifact | Produced by | Consumed by |
|---|---|---|
| `data/pages/*.md`, `data/index.jsonl` | `stratpoint_crawl` (out of scope) | `rag/loader.py` |
| `chroma_db/` (gitignored, regenerable) | `rag/ingest.py` via `rag/store.py` | `rag/retrieve.py`, and directly by `agent/guardrail_agent.py` for the contact/location `$contains` augmentation |
| `rag/eval/gold.jsonl` | hand-written | `rag/eval/run.py` |
| `evaluation/prompt_ablation_results.jsonl` | `prompts/run_ablation.py` | `evaluation/PROMPT_ENGINEERING_FINDINGS.md` |
| `llmops_traces.jsonl` (gitignored; path via `LLMOPS_LOG_PATH`) | `llmops/sink.py`, written at the `api/app.py` request boundary | `GET /metrics` via `llmops/metrics.py` |

## Invariants worth preserving

- **Corpus invariant.** A page is present when its manifest `status` is `ok` **or** `skipped`; change is detected via `content_hash`, never via `status == "ok"`. `rag/loader.py` and `rag/ingest.py` both encode this.
- **No import of `stratpoint_crawl`.** The corpus on disk is the entire contract between the two packages.
- **`prompts` must not import `rag` at runtime.** `builder.py` imports `Chunk` under `TYPE_CHECKING` only; `rag/answer.py` importing `prompts` is the allowed direction.
- **Query and ingestion must use the same embedder.** Both go through `rag/embeddings.get_embedder()`; `guardrails/output_guardrails.py` reuses it for the hallucination check so no second model is downloaded.
- **The user prompt is byte-identical across prompt variants** so the system prompt stays the sole independent variable in ablations.
- **Guardrail ordering: built-in first, NeMo second.** The regex/embedding checks cost nothing per call; NeMo is the optional LLM-powered second pass and its absence degrades gracefully via `ImportError`.
- **`retrieve(query, k)` is the agent-facing seam.** It, `build_prompt()`, and `run_with_guardrails()` are the three integration points other components should call; everything else is internal.
- **The proposal tools' Pydantic contracts are the team interface.** `agent/contracts.py` types are what the stub implementations in `agent/tools.py` are swapped out behind — replace the body, keep the signature, and neither `react.py` nor the API changes.
- **`llmops` stays dependency-free and one-directional.** Callers pass primitives in; it never imports another first-party package. It also never persists query text — telemetry rows carry metrics, not content.
- **Token usage is accumulated, not read from one response.** A turn can make several NIM calls, some nested inside tools, so every call site calls `add_usage()` and only the request boundary resets/pops.
- **`anchor_entity()` deliberately skips long inputs.** Rewriting pronouns in pasted source text broke `find_resource`'s near-verbatim lookups; the ~20-word cutoff is load-bearing, not a tuning knob.

> Tests (`tests/`, `RAG-UnitTests/`), build output, caches, and the generated `chroma_db/` store are intentionally omitted from the file reference.
