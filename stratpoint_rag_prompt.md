# System Architecture Analysis & Guardrails Implementation Prompt

**Task:** Analyze the current codebase for the Stratpoint customer service RAG chatbot and implement a Disambiguation and Guardrails pipeline.

---

## Architectural Analysis

### Current State

The codebase has two top-level packages with a deliberate ownership split:

```
src/
├── stratpoint_crawl/    # OWNER-MAINTAINED - sitemap-driven Playwright crawler
└── stratpoint_rag/      # the chatbot (this is where all work goes)
    ├── rag/             # BUILT — chunking, embeddings, Chroma store, retrieve(), ingest CLI, answer()
    ├── prompts/         # BUILT — system prompts, few-shot, CoT templates, builder, registry
    ├── disambiguation/  # SCAFFOLD — empty __init__.py only
    ├── guardrails/      # SCAFFOLD — empty __init__.py only
    ├── agent/           # SCAFFOLD — empty __init__.py only
    ├── api/             # SCAFFOLD — empty __init__.py only
    ├── ui/              # SCAFFOLD — empty __init__.py only
    └── evaluation/      # SCAFFOLD — empty __init__.py only
```

**Built modules:**
- `rag/` — 9 Python files: chunker, embeddings, Chroma vector store, retrieval seam, ingest CLI, answer() path. Uses `sentence-transformers` (BAAI/bge-small-en-v1.5) for embeddings, ChromaDB for storage, and NVIDIA NIM (`google/gemma-4-31b-it`) for generation.
- `prompts/` — 5 Python files: Pydantic schemas (`GroundedAnswer`, `Citation`), prompt builder with 6 variants (V0-V4), few-shot examples, registry with temperature configs.

**Scaffold modules** (planned, awaiting implementation):
- `disambiguation/` — scope: "Detect ambiguous user input and clarify intent before committing to retrieval or tool call."
- `guardrails/` — scope: "Input/output checks — off-topic and prompt-injection filtering on the way in, groundedness/safety checks on the way out."
- `agent/` — scope: "ReAct agent orchestrating retrieval and other tools."

### Current Flow (rag/answer.py)

```
User query → retrieve() → build_prompt() → NVIDIA NIM → Pydantic validate → formatted answer
```

`answer()` is a standalone monolith — no disambiguation, no guardrails, no memory, no multi-turn handling.

### Key Integration Points

| Layer | Where it plugs in | Why |
|---|---|---|
| **Disambiguation** | Before `retrieve()` — preliminary step in the agent orchestrator | Save retrieval calls on off-topic/unclear queries |
| **Slot Extraction** | After intent classification, before retrieval | Multi-turn needs session state (not currently tracked) |
| **Input Guardrails** | Wrapping the entire pipeline entry point | Must block/redact before any processing |
| **Output Guardrails** | After LLM response, before returning to user | Must scrub the final answer |
| **Conversation Memory** | Injected into prompt context alongside retrieved chunks | Needs summary buffer + vector persistence |

### Dependencies Currently Available

From `pyproject.toml`:
- `httpx`, `pydantic`, `chromadb`, `sentence-transformers`, `tenacity`, `python-dotenv`

Missing (to be added):
- `langchain` (for `ConversationSummaryBufferMemory`)
- `langchain-community` (for LangChain integrations)

---

## Implementation Plan

### Phase 1 — Disambiguation & Structured Outputs

**Files to create in `src/stratpoint_rag/disambiguation/`:**

#### 1.1 `schemas.py` — Pydantic models for the disambiguation layer

```python
# Intent categories the classifier can return
class IntentCategory(str, Enum):
    ASK_STRATPOINT = "ask_stratpoint"     # On-topic retrieval query
    GREETING = "greeting"                 # Simple greeting
    OFF_TOPIC = "off_topic"               # Outside Stratpoint domain
    NEEDS_CLARIFICATION = "needs_clarification"  # Ambiguous — needs follow-up
    HARMFUL = "harmful"                   # Malicious / prompt injection

# Classifier output
class IntentQuery(BaseModel):
    intent: IntentCategory
    confidence: float                     # 0.0–1.0
    reasoning: str                        # LLM's reasoning for the classification
    sub_intent: str | None = None         # More specific subclassification

# Slot definition per intent
class SlotDef(BaseModel):
    name: str
    description: str
    required: bool = True
    llm_hint: str | None = None           # Hint for the LLM extractor

# Slot extraction output
class SlotQuery(BaseModel):
    intent: IntentCategory
    slots: dict[str, Any]                 # Extracted slot values
    missing_slots: list[str]              # Slot names that need clarification

# Clarification turn in multi-turn loop
class ClarificationTurn(BaseModel):
    slot_name: str
    question: str
    answer: str

# Clarification loop state
class ClarificationSession(BaseModel):
    turns: list[ClarificationTurn] = []
    max_turns: int = 3
    intent: IntentCategory | None = None
    confirmed_slots: dict[str, Any] = {}

# Final routing result
class RouteResult(BaseModel):
    intent: IntentCategory
    confidence: float
    query: str                            # Original or rephrased query
    slots: dict[str, Any] = {}
    should_retrieve: bool = True          # False = handle without retrieval
    rejection_reason: str | None = None   # If intent is OFF_TOPIC / HARMFUL
```

#### 1.2 `classifier.py` — LLM-based intent classification

```
classify(user_input: str, conversation_context: str | None = None) -> IntentQuery
```

- Calls NVIDIA LLM with a structured JSON prompt (same pattern as `prompts/builder.py`)
- Returns `IntentQuery` with `confidence` score
- If `confidence < 0.7`, marks as `NEEDS_CLARIFICATION`
- Prompt includes Stratpoint domain context so the model knows what's on-topic

#### 1.3 `slots.py` — Slot definitions and extraction

```
INTENT_SLOTS: dict[IntentCategory, list[SlotDef]] = {
    ASK_STRATPOINT: [
        SlotDef(name="topic", description="What the user wants to know about", ...),
        SlotDef(name="project_name", description="Specific project if mentioned", ...),
    ],
    ...
}

extract_slots(user_input: str, intent: IntentCategory, history: list[ClarificationTurn] | None = None) -> SlotQuery
```

- Calls LLM to extract slot values from user message + clarification history
- Returns which slots are filled and which are missing

#### 1.4 `clarification.py` — Multi-turn clarification state machine

```
class ClarificationLoop:
    def __init__(self, intent: IntentCategory, missing_slots: list[str], max_turns: int = 3)
    def next_question(self) -> str | None          # LLM generates question for next missing slot
    def process_answer(self, answer: str) -> SlotQuery  # Re-extract slots with new info
    def is_complete(self) -> bool                  # All required slots filled or max turns reached
    def to_dict() -> dict                          # For session persistence
    @classmethod def from_dict(data: dict) -> ClarificationLoop
```

- Asks **one natural question at a time** (LLM-driven)
- Re-attempts slot extraction after each answer
- Forced completion after `max_turns` even if slots are still missing
- Graceful degradation: missing optional slots → continue; missing required slots → note and escalate

#### 1.5 `router.py` — Disambiguation orchestrator

```
route(user_input: str, session_id: str | None = None) -> RouteResult
```

1. Load or create `ClarificationSession` for `session_id`
2. If session has active clarification loop → process next turn
3. Otherwise → `classify()` the input
4. If `HARMFUL` → return early with rejection
5. If `OFF_TOPIC` → return early with out-of-scope message
6. If `NEEDS_CLARIFICATION` → start `ClarificationLoop`, return first question
7. If `ASK_STRATPOINT` → `extract_slots()`, if slots missing → start loop, else proceed
8. If `GREETING` → return early with greeting response

---

### Phase 2 — Guardrails & Memory Pipeline

**Files to create in `src/stratpoint_rag/guardrails/`:**

#### 2.1 `schemas.py` — Guardrail data models

```python
class RedactionRule(BaseModel):
    pattern: str                     # Regex pattern
    replacement: str                 # Replacement string (e.g. "[REDACTED]")
    entity_type: str                 # e.g. "email", "phone", "ssn"

class GuardrailResult(BaseModel):
    passed: bool
    action: Literal["allow", "block", "redact", "escalate"]
    message: str
    modified_input: str | None = None     # Redacted version if action == "redact"
    modified_output: str | None = None    # Scrubbed version for output guardrails

class GuardrailConfig(BaseModel):
    mode: Literal["fail_open", "fail_closed"] = "fail_closed"
    redact_pii: bool = True
    filter_topic: bool = True
    block_keywords: bool = True
    check_hallucination: bool = True
    check_advice: bool = True
```

#### 2.2 `input_guardrails.py` — Pre-LLM checks

**PIIRedactor:**
- Regex patterns: email (`[\w.+-]+@[\w-]+\.[\w.-]+`), phone (`\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}`), SSN (`\d{3}-\d{2}-\d{4}`), credit card
- `.redact(text) -> tuple[str, list[RedactedEntity]]`

**TopicFilter:**
- Checks if query is within Stratpoint business domain
- Uses LLM call with a lightweight prompt (or keyword heuristics as fallback)
- Stratpoint domain keywords: "outsystems", "flutter", "mobile dev", "web dev", "consulting", "stratpoint", "retail", "healthcare", etc.

**KeywordBlocker:**
- Blacklist of prompt-injection patterns: "ignore previous instructions", "system prompt", "you are now", "DAN", etc.
- `.check(text) -> GuardrailResult`

**InputPipeline:** Runs all three, returns first block or aggregated redact.

#### 2.3 `output_guardrails.py` — Post-LLM checks

**OutputPIIChecker:**
- Scans LLM response for leaked PII patterns
- Cross-references against retrieved source chunks — if PII appears in sources, it's legitimate
- `.check(response: str, source_chunks: list[Chunk]) -> GuardrailResult`

**HallucinationChecker (combined approach):**
- **Method 1 (LLM-as-judge):** Prompt LLM to verify: "Is each factual claim in this answer supported by the provided Stratpoint sources? List unsupported claims."
- **Method 2 (Semantic similarity):** Split answer into sentences/claims, embed each, find max cosine similarity to any chunk embedding. Flag claims below threshold (e.g. < 0.75).
- **Combined verdict:** Block if BOTH methods flag; warn if one flags; allow if neither flags.

**AdviceBlocker:**
- Pattern-based check for disallowed advice categories: legal, medical, financial, investment
- `.check(response: str) -> GuardrailResult`

**OutputPipeline:** Runs all three, returns most severe result.

#### 2.4 `pipeline.py` — Composable guardrail pipeline

```python
class GuardrailPipeline:
    def __init__(self, config: GuardrailConfig)
    def run_input(self, user_input: str) -> tuple[str, list[GuardrailResult]]
    def run_output(self, llm_response: str, source_chunks: list[Chunk]) -> tuple[str, list[GuardrailResult]]
```

- `fail_closed`: blocks if any guardrail fails
- `fail_open`: logs warning but allows passage

#### 2.5 `memory.py` — Dual memory system

```python
class ConversationMemory:
    def __init__(self, session_id: str, chroma_collection: Collection | None = None)
    
    # Summary buffer (LangChain ConversationSummaryBufferMemory)
    def add_turn(self, user_input: str, assistant_response: str)
    def get_summary(self) -> str
    
    # Cross-session semantic store (ChromaDB)
    def persist_turn(self, user_input: str, assistant_response: str, metadata: dict)
    def search_history(self, query: str, k: int = 3) -> list[dict]
    
    # Context builder — merges summary + semantically relevant history
    def get_context(self, current_query: str) -> str
```

- Summary buffer tracks the active session (in-memory, LRU-evicted)
- ChromaDB collection stores past sessions keyed by `session_id` + timestamp
- Context builder returns: "Previous conversation summary: ... \n\nRelevant past interactions: ..."

---

### Phase 3 — Agent Orchestrator

**Files to create in `src/stratpoint_rag/agent/`:**

#### 3.1 `orchestrator.py` — Main ReAct loop

```python
class Agent:
    def __init__(self, guardrail_config: GuardrailConfig | None = None)
    
    def orchestrate(self, user_input: str, session_id: str | None = None) -> AnswerResult:
        """
        1. Load conversation memory for session_id
        2. InputPipeline → redact/filter/block
        3. Disambiguation → classify → clarify → extract
        4. If OFF_TOPIC / HARMFUL / GREETING → return early
        5. Retrieve k chunks
        6. Build prompt: system + memory context + retrieved chunks + user query
        7. Call NVIDIA LLM with structured output (GroundedAnswer schema)
        8. OutputPipeline → PII check + hallucination check + advice check
        9. Update conversation memory
        10. Return final answer with citations
        """
```

**AnswerResult:**
```python
class AnswerResult(BaseModel):
    answer: str
    citations: list[Citation]
    intent: IntentCategory
    guardrail_results: list[GuardrailResult]
    clarification_needed: bool = False
    clarification_question: str | None = None
```

---

### Files NOT Modified

- `rag/retrieve.py` — kept as clean seam for the agent
- `rag/embeddings.py` — reused for semantic hallucination check
- `rag/store.py` — reused for memory vector store
- `prompts/*` — reused for prompt building
- `stratpoint_crawl/*` — left entirely alone (per CLAUDE.md)
- `docs/general-log.md` — only the `update-log` skill touches this

---

## NVIDIA NeMo Guardrails Evaluation

| Factor | NeMo Guardrails | Custom Python (chosen) |
|---|---|---|
| **Setup overhead** | Heavy — Colang files, YAML config, separate runtime | Light — plain Python modules importing existing code |
| **Deterministic control** | Colang flows add indirection, framework-managed state | Full control — exact logic in Python |
| **Integration with existing code** | Must wrap the LLM call; NeMo manages its own state | Direct — imports `stratpoint_rag.rag.retrieve`, uses existing `httpx` + Pydantic |
| **PII redaction** | Built-in but limited customization | Regex — fully customized to Stratpoint domain |
| **Multi-turn clarification** | Colang flows support this | Simple Python state machine — easier to debug and test |
| **Maintenance overhead** | Depends on NeMo releases, Colang version | Zero external dependency for guardrails logic |
| **Deployment (LXC container)** | Heavy — NeMo adds Docker overhead | Lightweight — just Python modules |

**Verdict:** NeMo Guardrails is overkill. The codebase is lightweight (no LangChain/LlamaIndex dependency), uses direct `httpx` calls to NVIDIA NIM, and already has Pydantic validation. Adding NeMo would introduce a second runtime, a custom DSL (Colang), and complex configuration for what amounts to ~500 lines of Python. **Custom Python modules** with optional LangChain `ConversationSummaryBufferMemory` (single import) is the right approach.

---

## Dependency Changes

Add to `pyproject.toml`:
```toml
"langchain>=0.3.0",
"langchain-community>=0.3.0",
```

---

## Testing Strategy

Create `RAG-UnitTests/test_disambiguation/` and `RAG-UnitTests/test_guardrails/`:

| Test file | What it tests |
|---|---|
| `test_classifier.py` | `classify()` returns correct IntentCategory for known patterns; confidence threshold handling |
| `test_slots.py` | Slot extraction for various queries; missing slot detection |
| `test_clarification.py` | Multi-turn loop: question generation, answer processing, completion, max-turn limit |
| `test_router.py` | Full routing: off-topic rejection, clarification initiation, slot completion |
| `test_input_guardrails.py` | PII redaction, topic filter, keyword blocker — each with edge cases |
| `test_output_guardrails.py` | Hallucination check (both methods), PII leak detection, advice blocking |
| `test_pipeline.py` | GuardrailPipeline integration: fail-closed blocks, fail-open allows |
| `test_memory.py` | Summary buffer, ChromaDB persistence, context building |
| `test_orchestrator.py` | Full Agent.orchestrate() integration test with mocked components |

Run: `uv run pytest` to confirm zero crawler test regressions + new tests pass.

---

## Self-Log

Created at `docs/Mikhos_self-log.md` — following the established format from KEISHA_self-log.md and VIENN_self-log.md (newest entry at top, bullet-point style).
