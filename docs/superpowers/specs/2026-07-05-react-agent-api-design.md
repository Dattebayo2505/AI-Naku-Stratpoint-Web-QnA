# ReAct Agent + API Endpoint — Design Spec

**Date:** 2026-07-05
**Author:** Sean Kyle Dimaunahan
**Components owned:** `stratpoint_rag.agent` (ReAct agent), `stratpoint_rag.api` (HTTP endpoint)
**Status:** Approved design — pending implementation plan

---

## 1. Purpose & scope

Build the **ReAct agent** and the **API endpoint** for the Stratpoint website customer-support bot.
The agent is a reason/act loop that orchestrates a small set of tools to serve website
visitors; the API exposes it over HTTP so the (teammate-owned) Streamlit UI can consume it.

This spec covers **only** those two components. Everything else is a seam the agent calls or a
wrapper around the API — see the boundary table.

### Ownership boundary

| Concern | Owner | This spec's relationship |
| --- | --- | --- |
| ReAct agent (`agent/`) | **me** | build it |
| API endpoint (`api/`) | **me** | build it |
| RAG `retrieve()` / `answer()` | teammate | **reuse** as tools/answer engine |
| Prompt engineering / `GroundedAnswer` | teammate | reused inside `rag.answer()` |
| Disambiguation | teammate | **defer** — agent may answer or ask naturally, but ambiguity *detection* is theirs |
| Guardrails | teammate | **defer** — wraps this API in/out |
| Chat UI | teammate | **consumes** this API |

Non-negotiable seam rules (from project CLAUDE.md): `stratpoint_rag` must not import from
`stratpoint_crawl`; `prompts` must not import `rag`. The agent lives in `stratpoint_rag.agent`
and imports `stratpoint_rag.rag` (allowed).

---

## 2. Use case

A visitor to stratpoint.com chats with the bot. Three visitor jobs, each mapping onto a
lecture stress-test class (06b §5):

| Visitor says | Agent does | ReAct class |
| --- | --- | --- |
| "Do you help with cloud migration?" | `search_stratpoint` → grounded answer | tool-using |
| "Got anything I can read on supply-chain + cloud?" | `find_resource` → returns the AWS whitepaper PDF link from the corpus | tool-using |
| "Do you do cloud *and* have retail case studies?" | two `search_stratpoint` calls, then answer | multi-hop |
| "Hi, what is this bot?" | answer directly, **no tool** | tool-free |

The `find_resource` capability is grounded in a real corpus property: blog pages embed
downloadable PDF links (e.g. the AWS availability whitepaper in
`data/pages/2023__08__29__boost-supply-chain-resilience-and-efficiency-through-cloud-data-and-ai.md`
line 28; the WEF Global Risks Report in
`data/pages/2024__09__13__evaluating-your-company-digital-maturity.md` line 40).

---

## 3. Agent design

### 3.1 Framework decision (settled)

Use **LangChain**, wired to the NVIDIA NIM **cloud** endpoint:

- **LLM:** `ChatNVIDIA` from `langchain-nvidia-ai-endpoints` — purpose-built for
  `https://integrate.api.nvidia.com/v1`. Configured from the existing `rag.config` values
  (`nvidia_base_url()`, `llm_model()`, `nvidia_api_key()`), **not** re-hardcoded.
- **Loop:** `create_react_agent(model=llm, tools=[...])` from **`langgraph.prebuilt`**.

**Two mandatory deviations from the lecture code (06 §4):**

1. Swap `ChatOllama` → `ChatNVIDIA` (lecture uses local Ollama; we use the cloud NIM).
2. Swap `langchain.agents.create_agent` → `langgraph.prebuilt.create_react_agent`.
   NVIDIA's tool-calling docs explicitly warn: *"Do not use `langchain.agents.create_agent`
   with `ProviderStrategy` for tool calling. This pattern bypasses the tool execution loop."*

### 3.2 Why this is safe

Empirically verified (2026-07-05) against the real config: `google/gemma-4-31b-it` at
`https://integrate.api.nvidia.com/v1` returns proper OpenAI-style tool calls — HTTP 200,
`finish_reason: "tool_calls"`, populated `message.tool_calls[]`. Native tool calling is
enabled server-side on the cloud endpoint, so no regex/text-parsing fallback is needed.

> Note: NVIDIA's `--enable-auto-tool-choice` / `--tool-call-parser` flags apply to the
> **self-hosted** NIM container (the future LXC/Docker path), not the current cloud endpoint.
> If the project later moves the model in-container, tool calling must be re-enabled there.

### 3.3 Tools (all owned here — no teammate overlap)

Defined as LangChain `@tool` functions; LangChain derives each schema from type hints +
docstring, so **docstrings are load-bearing** (they drive tool selection).

1. **`search_stratpoint(query: str) -> str`**
   Thin wrapper over `rag.answer(query)`. Returns the grounded answer string (already
   includes inline "Sources used" citations). This is the reuse of the prompt-engineering
   teammate's engine — the agent does **not** re-implement grounded generation.

2. **`find_resource(topic: str) -> str`**
   Calls `rag.retrieve(topic, k=5)` (reusing the `retrieve` default), scans each returned
   `Chunk.text` (markdown) for downloadable document links — primarily `[label](https://….pdf)`,
   also `.doc(x)`/`.ppt(x)` if present — and returns a compact list of `title — url` entries
   (plus the source page URL). Returns a clear
   "no downloadable resources found" message when none match. No new data pipeline; grounded
   purely in retrieved corpus chunks.
   *Known caveat:* many corpus PDFs are third-party citations (AWS, WEF, McKinsey), not
   Stratpoint-owned assets. Acceptable for "here's the whitepaper our article referenced";
   documented so it is a deliberate choice, not a surprise.

### 3.4 System prompt

A concise customer-support system prompt that:
- Frames the bot as Stratpoint's website assistant.
- Instructs: use `search_stratpoint` for questions about Stratpoint's services / company /
  blog; use `find_resource` when the visitor wants something to read/download.
- Instructs: answer greetings/meta directly with no tool (tool-free path).
- Instructs: base factual claims on tool results, not memory.

Ambiguity handling stays lightweight (ask for a missing detail in plain language when a query
lacks it); systematic ambiguity **detection** is the disambiguation teammate's component and is
out of scope.

### 3.5 Agent runner & public seam

`agent/agent.py` exposes a single public function, e.g.:

```
run_agent(message: str, history: list | None = None) -> AgentResult
```

`AgentResult` (Pydantic) carries:
- `answer: str` — final assistant text (already contains inline citations via `rag.answer`).
- `trace: list[Step]` — the Thought/Action/Observation steps, captured by streaming the
  LangGraph agent (`.stream(..., stream_mode="values")`, per lecture 06 §4). Cheap to collect,
  valuable for the demo and the UI.
- `citations: list` — best-effort structured URLs extracted from `search_stratpoint`
  observations captured during the stream.
- `resources: list` — structured `{title, url}` captured from `find_resource` observations.

`citations`/`resources` are populated from the captured tool observations in `trace`, not by
re-parsing the final answer text. They are best-effort (documented as such): the authoritative
answer, including citations, is always in `answer`.

A `recursion_limit` (max tool iterations) caps the loop to prevent runaway execution
(the lecture's `max_turns` equivalent).

`agent/__init__.py` re-exports `run_agent` and `AgentResult` as the module's public surface —
the API layer imports only these.

---

## 4. API endpoint design

`api/app.py` — a FastAPI app exposing the agent. Deps to add: `fastapi`, `uvicorn`.

### 4.1 Contract

`POST /chat`

Request (Pydantic-validated):
```json
{ "message": "string", "history": [ ... ]?, "session_id": "string?" }
```

Response:
```json
{
  "answer": "string",
  "citations": [ ... ],
  "resources": [ { "title": "string", "url": "string" } ],
  "trace": [ { "thought": "string?", "action": "string?", "action_input": {}, "observation": "string?" } ]
}
```

- `history` and `session_id` are optional (accepted now, threaded into the agent when present;
  full multi-turn memory can be a later increment).
- The endpoint is a thin adapter: validate request → `run_agent(...)` → serialize `AgentResult`.

A `GET /health` returns a simple liveness JSON (used by the LXC deployment / smoke checks).

`api/__init__.py` re-exports the FastAPI `app`. The guardrails teammate wraps this endpoint
(the lecture's `POST /guardrail` is **their** deliverable, not this one). The UI teammate calls
`POST /chat`.

### 4.2 Running it

Local: `uv run uvicorn stratpoint_rag.api.app:app --port <PORT>`. A console-script entry
(`stratpoint-rag-api`) may be added for parity with existing scripts. On the LXC target the
same ASGI app runs behind uvicorn; `PORT` comes from `.env` (already an established env var).

---

## 5. Data flow

```
UI → POST /chat → api.app (validate)
        → agent.run_agent(message)
             → create_react_agent loop (ChatNVIDIA):
                  Thought → tool call → Observation → …:
                    search_stratpoint → rag.answer → retrieve + build_prompt + NIM
                    find_resource     → rag.retrieve → parse PDF links from chunks
             → capture answer + trace + citations + resources
        → serialize AgentResult → JSON response → UI
```

Important: **do not** combine `response_format={"type":"json_object"}` with `tools` in the same
request. Tool *selection* is driven by `tools`/`tool_choice` in the agent loop; structured
`GroundedAnswer` JSON stays inside `rag.answer()`'s own final-answer call. Clean separation —
the agent does routing, `rag.answer()` does grounded generation.

---

## 6. Error handling

- **Tool errors** (bad input, no corpus match): the tool catches and
  returns an error/empty-result **string** as its Observation (lecture pattern), so the loop
  continues and the model can recover or explain — a tool failure never crashes the request.
- **LLM / endpoint failures** (NIM down, auth error, timeout): surface from `run_agent` as an
  exception; the API maps them to `502`/`503` with a safe JSON error body — no stack traces to
  the client.
- **Missing `NVIDIA_API_KEY`:** fail fast with a clear message (mirrors `rag.answer`'s existing
  `RuntimeError`).
- **Request validation:** malformed `POST /chat` bodies → FastAPI/Pydantic `422` automatically.
- **Runaway loops:** bounded by the agent's `recursion_limit`.

---

## 7. Testing

Unit tests (offline, no network — matches the repo's `-m 'not integration'` default):

- **Tools in isolation:**
  - `find_resource` — feed a fixture `Chunk` whose `text` contains a `.pdf` link (mirroring the
    two real corpus pages); assert the link is extracted; assert the no-match message when
    absent. `retrieve` is monkeypatched.
  - `search_stratpoint` — assert it delegates to `rag.answer` (mocked), returning its string.
- **Agent runner:** with tools/LLM stubbed (or a fake model), assert `run_agent` returns a
  populated `AgentResult` and that `trace`/`resources` reflect the tool observations. Assert the
  tool-free path returns an answer with no tool steps.
- **API:** FastAPI `TestClient` with `run_agent` monkeypatched — assert `POST /chat` validates
  input, returns the serialized shape, and maps agent exceptions to the right status codes;
  assert `GET /health`.

Integration (opt-in, `-m integration`): one live `POST /chat` against the real NIM endpoint
that exercises a `search_stratpoint` call end-to-end. Deselected by default.

---

## 8. Dependencies to add

`langchain-nvidia-ai-endpoints`, `langgraph`, `langchain-core`, `fastapi`, `uvicorn`.
(No LangChain-Ollama — the lecture's Ollama path is not used.)

---

## 9. Out of scope (explicit)

- Disambiguation detection, guardrail filtering, and the chat UI (teammates).
- Multi-turn conversation memory beyond accepting an optional `history` field.
- Lead capture / callback (`request_callback` → `data/leads.jsonl`) — deferred to a later increment.
- A curated resource catalog or CRM lead backend.
- Self-hosted in-container NIM tool-calling config (future LXC/Docker deployment task).
```
