# Architecture & System Flow — Stratpoint RAG Chatbot

> **Defense-ready document** explaining how the Disambiguation & Guardrails pipeline integrates with the existing RAG system.
> **Your module:** `disambiguation/`, `guardrails/`, `agent/orchestrator.py`

---

## 1. High-Level System Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              USER INPUT                      │
                    │   "What services does Stratpoint offer?"     │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │         1. INPUT GUARDRAILS                  │
                    │    ┌─────────┐  ┌──────────┐  ┌──────────┐  │
                    │    │  PII    │  │  Topic   │  │ Keyword  │  │
                    │    │Redactor │  │ Filter   │  │ Blocker  │  │
                    │    └─────────┘  └──────────┘  └──────────┘  │
                    └──────────────────┬──────────────────────────┘
                                       │ (redacted input)
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │       2. DISAMBIGUATION PIPELINE              │
                    │                                              │
                    │   ┌──────────────┐                           │
                    │   │   Classify   │──→ GREETING ──→ Early exit│
                    │   │   Intent     │──→ HARMFUL  ──→ Block     │
                    │   └──────┬───────┘──→ OFF_TOPIC──→ Reject    │
                    │          │            ──────────────────────  │
                    │          ▼                                    │
                    │   ┌──────────────┐    ┌──────────────────┐   │
                    │   │    Extract   │    │ Clarification    │   │
                    │   │    Slots     │──→│ Loop (max 3)     │   │
                    │   └──────┬───────┘    └──────────────────┘   │
                    │          │                                     │
                    │          ▼                                     │
                    │   ┌──────────────┐                            │
                    │   │    Route     │──→ ask_stratpoint + filled │
                    │   └──────────────┘    slots + query           │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │        3. RAG RETRIEVAL                       │
                    │   retrieve(query, k=5) → top-k Chunks        │
                    │   (existing module, reused as-is)             │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │        4. PROMPT BUILDING + MEMORY            │
                    │   ┌──────────────┐  ┌──────────────────┐    │
                    │   │  Build Prompt │  │ Conversation     │    │
                    │   │  (system +   │  │ Memory (summary  │    │
                    │   │   context)   │  │ + vector store)  │    │
                    │   └──────────────┘  └──────────────────┘    │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │        5. LLM GENERATION                      │
                    │   NVIDIA NIM (google/gemma-4-31b-it)          │
                    │   response_format = {"type": "json_object"}   │
                    │   → GroundedAnswer {answer, citations, ...}   │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │       6. OUTPUT GUARDRAILS                    │
                    │  ┌──────────┐ ┌──────────────┐ ┌──────────┐  │
                    │  │  PII     │ │ Hallucination│ │ Advice   │  │
                    │  │  Leak    │ │ Check (LLM + │ │ Blocker  │  │
                    │  │  Check   │ │  Semantic)   │ │          │  │
                    │  └──────────┘ └──────────────┘ └──────────┘  │
                    └──────────────────┬──────────────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │              FINAL ANSWER                     │
                    │   "Stratpoint offers..."                      │
                    │   Sources: - Services (stratpoint.com/...)    │
                    └─────────────────────────────────────────────┘
```

---

## 2. Module Responsibility Breakdown

### `disambiguation/` — Your module

| File | Responsibility | Key Classes/Functions |
|---|---|---|
| `schemas.py` | Pydantic data models for all disambiguation types | `IntentCategory` (enum), `IntentQuery`, `SlotQuery`, `ClarificationSession`, `RouteResult` |
| `classifier.py` | LLM-based intent classification with heuristic fallback | `classify()`, `_heuristic_fallback()`, `_call_llm()` |
| `slots.py` | Slot definitions per intent + LLM extraction | `INTENT_SLOTS` (dict), `extract_slots()` |
| `clarification.py` | Multi-turn state machine (max 3 turns) | `ClarificationLoop` class: `next_question()`, `process_answer()`, `is_complete()`, `to_dict()` / `from_dict()` |
| `router.py` | Orchestrates classify → clarify → extract → route | `route()`, `_active_loops` (session registry) |

### `guardrails/` — Your module

| File | Responsibility | Key Classes/Functions |
|---|---|---|
| `schemas.py` | Config and result models | `RedactionRule`, `GuardrailResult`, `GuardrailConfig` |
| `input_guardrails.py` | Pre-LLM safety checks | `PIIRedactor`, `TopicFilter`, `KeywordBlocker`, `InputPipeline` |
| `output_guardrails.py` | Post-LLM safety checks | `OutputPIIChecker`, `HallucinationChecker` (LLM-judge + semantic), `AdviceBlocker`, `OutputPipeline` |
| `pipeline.py` | Composable pipeline with fail-open/fail-closed | `GuardrailPipeline.run_input()`, `run_output()` |
| `memory.py` | Dual-memory conversation system | `SummaryBuffer` (custom), `ConversationMemory` (in-memory + ChromaDB) |

### `agent/` — Your module

| File | Responsibility | Key Classes/Functions |
|---|---|---|
| `orchestrator.py` | Production entry point — wires all layers | `Agent.orchestrate()`, `AnswerResult` |

### Existing modules (reused, not modified)

| Package | What it provides | How we use it |
|---|---|---|
| `rag/` | Chunking, embeddings, ChromaDB, retrieval | `retrieve()` for document lookup; `embeddings` for semantic hallucination check |
| `prompts/` | Prompt templates, Pydantic schemas | `build_prompt()` for LLM prompt construction; `GroundedAnswer` for structured output |

---

## 3. Two-Layer Guardrail Architecture

```
                    INPUT LAYER                          OUTPUT LAYER
    ┌──────────────────────────────┐      ┌──────────────────────────────┐
    │   What gets checked          │      │   What gets checked          │
    │                              │      │                              │
    │  ┌────────────────────┐      │      │  ┌────────────────────┐      │
    │  │ 1. PII Redaction   │      │      │  │ 1. PII Leak Check  │      │
    │  │    email → [EMAIL] │      │      │  │    Cross-references │      │
    │  │    phone → [PHONE] │      │      │  │    source docs      │      │
    │  │    SSN   → [SSN]   │      │      │  └────────────────────┘      │
    │  └────────────────────┘      │      │  ┌────────────────────┐      │
    │  ┌────────────────────┐      │      │  │ 2. Hallucination   │      │
    │  │ 2. Topic Filter    │      │      │    Check              │      │
    │  │    Stratpoint domain │    │      │    ├─ LLM-as-judge    │      │
    │  │    or off-topic?   │      │      │    └─ Semantic sim    │      │
    │  └────────────────────┘      │      │  └────────────────────┘      │
    │  ┌────────────────────┐      │      │  ┌────────────────────┐      │
    │  │ 3. Keyword Blocker │      │      │  │ 3. Advice Blocker  │      │
    │  │    prompt injection│      │      │    ├─ Medical          │      │
    │  │    jailbreak       │      │      │    ├─ Legal            │      │
    │  │    system prompt   │      │      │    └─ Financial        │      │
    │  └────────────────────┘      │      │  └────────────────────┘      │
    └──────────────────────────────┘      └──────────────────────────────┘
```

**Key design choice — combined hallucination check:**

Both methods must agree before we block; if one flags and the other doesn't, we append a disclaimer note to the response. This avoids blocking legitimate answers while still being transparent with the user.

```
LLM-as-judge       Semantic Similarity     Result
─────────────────────────────────────────────────
No flags           No flags               ✅ Clean response
Flags              No flags               ⚠️ Response + verification note appended
No flags           Flags                  ⚠️ Response + verification note appended
Flags              Flags                  🚫 BLOCK/ESCALATE to human review
```

The verification note reads: *"Some claims in this answer could not be fully verified against our source documents. Please verify important information before acting on it."*

---

## 4. Clarification Loop Sequence

```
User: "Tell me about Stratpoint"
          │
          ▼
    ┌─────────────┐
    │  Classify   │──→ Intent: ASK_STRATPOINT (conf: 0.75)
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │   Extract   │──→ missing_slots: ["topic"]
    │   Slots     │
    └──────┬──────┘
           │
           ▼
    ┌──────────────────┐
    │Start Clarification│
    │Loop (max=3 turns) │
    └──────┬───────────┘
           │
      ┌────┴────┐
Turn 1│  LLM    │──→ "What would you like to know about Stratpoint?"
      │generates│
      └────┬────┘
           │
      User: "I want to know about their Flutter projects"
           │
           ▼
    ┌─────────────┐
    │  Re-extract │──→ slots: {topic: "Flutter projects"}
    │  Slots      │──→ missing_slots: []
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐
    │  Complete   │──→ Route to retrieval
    └─────────────┘
```

If the user still doesn't provide enough information after 3 turns, the loop auto-completes and the system proceeds with whatever slots were filled (graceful degradation).

---

## 5. Dual Memory System

```
┌─────────────────────────────────────────────────┐
│              ConversationMemory                   │
│                                                   │
│  ┌─────────────────────┐  ┌──────────────────┐   │
│  │  IN-MEMORY BUFFER     │  │  PERSISTENT       │   │
│  │  (SummaryBuffer)     │  │  STORE (ChromaDB) │   │
│  ├─────────────────────┤  ├──────────────────┤   │
│  │ Active session only  │  │ Cross-session     │   │
│  │ LRU-evicted          │  │ Keyed by session  │   │
│  │ LLM-compressed       │  │ Semantic search   │   │
│  │ Last N turns +       │  │ Past interactions │   │
│  │ running summary      │  │ retrievable by    │   │
│  └─────────────────────┘  │ similarity         │   │
│                           └──────────────────┘   │
│                                                   │
│  get_context(query):                              │
│    "Previous conversation summary: ...            │
│     Relevant past interactions: ..."              │
└─────────────────────────────────────────────────┘
```

When the token budget is exceeded, the oldest turns are compressed into a running summary via an LLM call — only the most recent turn stays verbatim.

---

## 6. Fail-Closed vs Fail-Open

Controlled by `GuardrailConfig.mode`:

```
fail_closed (default):  ANY guardrail block → response blocked
                        "I generated a response, but it failed safety checks."

fail_open:              Blocked guardrails are logged but allowed through
                        Used during development/testing
```

---

## 7. NeMo Guardrails Evaluation Summary

| Factor | NeMo Guardrails | Our Custom Approach |
|---|---|---|
| Lines of code | ~500+ Colang + YAML | ~600 Python |
| Dependencies | NeMo framework + Colang runtime | Only existing deps (httpx, pydantic, chromadb) |
| Debugging | Framework indirection | Direct Python — step through in debugger |
| Control | State machines in Colang DSL | Deterministic if/else logic |
| PII | Pre-built but limited | Full regex control, easily extendable |
| Hallucination | Not built-in | Combined LLM + semantic check |
| Multi-turn | Colang flows | Simple state machine, testable in isolation |
| Deployment | Heavy (Docker + runtime) | Lightweight (Python modules) |

**Verdict:** Custom Python wins for our use case — the codebase is lightweight (no LangChain dependency), uses direct httpx calls, and already has Pydantic validation. Adding NeMo would introduce a second runtime and a custom DSL for what amounts to clean Python logic.

---

## 8. Data Flow Summary

```
User Input ("What does Stratpoint do with OutSystems?")
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  INPUT GUARDRAILS                      ║
      ║  ├─ PII Redact: no PII → unchanged     ║
      ║  ├─ Topic Filter: "OutSystems" ✓       ║
      ║  └─ Keyword Block: clean ✓              ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  DISAMBIGUATION                        ║
      ║  ├─ Classify: ASK_STRATPOINT (0.95)   ║
      ║  ├─ Extract: {topic: "OutSystems"}    ║
      ║  └─ Route: retrieve=true + slots      ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  RETRIEVAL (existing)                  ║
      ║  └─ top-5 chunks on "OutSystems"      ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  PROMPT + MEMORY                       ║
      ║  ├─ Memory: conversation summary      ║
      ║  ├─ Chunks: [Source: OutSystems]      ║
      ║  └─ System: v4_combined_lowtemp      ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  LLM (NVIDIA NIM)                      ║
      ║  └─ GroundedAnswer {answer, cite}     ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  OUTPUT GUARDRAILS                     ║
      ║  ├─ PII: none → allow                 ║
      ║  ├─ Hallucination: supported → allow  ║
      ║  └─ Advice: none → allow              ║
      ╚═══════════════════════════════════════╝
    │
    ▼ ╔═══════════════════════════════════════╗
      ║  FORMATTED ANSWER                      ║
      ║  "Stratpoint builds on OutSystems..."   ║
      ║  Sources used:                         ║
      ║  - OutSystems Offerings (stratpoint..)  ║
      ╚═══════════════════════════════════════╝
```

---

## 9. Key Files Reference

| File | What to show during defense |
|---|---|
| `src/stratpoint_rag/agent/orchestrator.py` | `Agent.orchestrate()` — the main flow showing all 9 steps |
| `src/stratpoint_rag/disambiguation/router.py` | `route()` — the disambiguation state machine |
| `src/stratpoint_rag/disambiguation/classifier.py` | `classify()` — LLM + heuristic fallback |
| `src/stratpoint_rag/disambiguation/clarification.py` | `ClarificationLoop` — multi-turn logic |
| `src/stratpoint_rag/guardrails/input_guardrails.py` | `PIIRedactor`, `KeywordBlocker` — input safety |
| `src/stratpoint_rag/guardrails/output_guardrails.py` | `HallucinationChecker` — combined LLM + semantic |
| `src/stratpoint_rag/guardrails/memory.py` | `SummaryBuffer` — custom LangChain replacement |

---

## 10. Quick Demo Commands

```bash
# Greeting → early return (no API key)
uv run python -c "
from stratpoint_rag.router import route
r = route('Hello!')
print(r.intent.value, r.should_retrieve)
"

# Check PII redaction
uv run python -c "
from stratpoint_rag.guardrails.input_guardrails import PIIRedactor
p = PIIRedactor()
print(p.redact('Contact: john@test.com or +639171234567'))
"

# Full pipeline (needs NVIDIA_API_KEY in .env)
uv run python -c "
from stratpoint_rag.agent.orchestrator import Agent
a = Agent()
r = a.orchestrate('What does Stratpoint do with OutSystems?')
print(r.answer)
"
```
