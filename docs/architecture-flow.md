# Stratpoint RAG Chatbot — System Architecture

## Overview

A RAG (Retrieval-Augmented Generation) chatbot for `stratpoint.com` that answers visitor questions about Stratpoint's services, projects, and technologies. The system uses a multi-stage pipeline: crawled website content is embedded into a vector database, user questions are classified and sanitized, relevant context is retrieved, and a language model generates grounded answers with citations.

## End-to-End Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER (Streamlit UI)                               │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │ POST /chat { message, session_id, use_nemo }
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          FastAPI (api/app.py)                                │
│  run_with_guardrails(message, history, session_id, use_nemo=True) → AgentResult│
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │
                     ┌─────┴─────┐
                     │ use_nemo? │
                     └──┬───┬────┘
                        │   │
              ┌─────────┘   └──────────┐
              ▼                        ▼
┌─────────────────────┐   ┌──────────────────────────┐
│  NeMoGuardrail      │   │  GuardrailPipeline       │
│  Pipeline           │   │  (fallback)              │
│  (nemo_guardrails   │   │  (pipeline.py)           │
│   .py → main.co)    │   │                          │
│  Colang 2.x flows:  │   │  InputPipeline:          │
│  • PII redact       │   │  • PIIRedactor           │
│  • Relevance check  │   │  • KeywordBlocker        │
│  • Self-check input │   │  • TopicFilter           │
│  • Jailbreak detect │   │                          │
└──────────┬──────────┘   └──────────┬───────────────┘
           │                         │ cleaned input
           └──────────┬──────────────┘
                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Phase 2: DISAMBIGUATION (disambiguation/)                                    │
│                                                                               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                    │
│  │  Classifier  │───▶│   Router     │───▶│ Clarification│                    │
│  │(heuristic +  │    │              │    │  Loop (max 3)│                    │
│  │ LLM fallback)│    │              │    │              │                    │
│  └──────────────┘    └──────┬───────┘    └──────────────┘                    │
│                             │                                                │
│              ┌──────────────┼──────────────┐                                │
│              ▼              ▼              ▼                                │
│         GREETING       OFF_TOPIC /    NEEDS_CLARIFY                         │
│         (canned       HARMFUL        (clarification                         │
│         response)     (rejection)    question back)                         │
│                                              │                              │
│              ASK_STRATPOINT ◄────────────────┘                              │
│              (proceed to answer)                                            │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │ query
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Phase 3: ANSWER                                                             │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  If resource keywords: ReAct Agent (agent/agent.py + tools.py)      │    │
│  │    → search_stratpoint(query) or find_resource(topic)               │    │
│  │  If simple Q&A: rag_answer(query) directly (1 LLM call)            │    │
│  │    → retrieve chunks → build_prompt → LLM → GroundedAnswer         │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│  Both paths return (answer_text, source_chunks) to output guardrails         │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │ response + source chunks
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Phase 4: OUTPUT GUARDRAILS                                                  │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  NeMo (default):                                                     │    │
│  │   • Output PII check (cross-reference source)                        │    │
│  │   • Advice blocker (medical/legal/financial directive patterns)       │    │
│  │   • Hallucination checker (embedding cosine sim)                     │    │
│  │   • Self-check hallucination (NeMo LLM-based)                        │    │
│  │   • Self-check output (NeMo LLM-based)                               │    │
│  │                                                                      │    │
│  │  Built-in (fallback):                                                │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ OutputPII    │    │Hallucination │    │AdviceBlocker │           │    │
│  │  │ Checker      │───▶│  Checker     │───▶│  (source-    │           │    │
│  │  │ (regex +     │    │ (embedding   │    │  aware)      │           │    │
│  │  │  source diff)│    │  cosine sim) │    │              │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │ safe response
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Phase 5: MEMORY + RESPONSE                                                  │
│                                                                              │
│  ┌────────────────────┐   ┌──────────────────────────────────────────────┐  │
│  │ ConversationMemory │   │ AgentResult { answer, citations, trace }     │  │
│  │ (last 6 turns)     │   │ → FastAPI → Streamlit UI                     │  │
│  └────────────────────┘   └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Module Map

| Package | Status | Owner | Purpose |
|---|---|---|---|
| `stratpoint_crawl` | Live | Dattebayo | Sitemap-driven Playwright crawler → `data/pages/*.md` |
| `stratpoint_rag.rag` | Built | Vienn | Chunking, embeddings, Chroma store, retrieve(), ingest CLI |
| `stratpoint_rag.prompts` | Built | Keisha | System-prompt variants, few-shot examples, GroundedAnswer schema |
| `stratpoint_rag.agent` | Built | (team) | LangGraph ReAct agent with search + resource tools |
| `stratpoint_rag.guardrails` | **Built** | **Mikhos** | Input/output safety checks, PII redaction, hallucination detection, NeMo backend (default) |
| `stratpoint_rag.disambiguation` | **Built** | **Mikhos** | Intent classification, slot extraction, clarification loop |
| `stratpoint_rag.api` | Built | (team) | FastAPI endpoint (/chat, /health) |
| `stratpoint_rag.ui` | Built | Keisha | Streamlit chat UI with debug panel |
| `stratpoint_rag.evaluation` | Scaffold | — | Retrieval and answer-quality evals |

## Guardrails Deep-Dive

### Design Philosophy
**Heuristic-first, LLM-as-fallback.** All guardrail checks run locally using regex patterns and keyword matching. The LLM is only called when heuristics are inconclusive (e.g., topic filter on an ambiguous query). This keeps latency low and avoids unnecessary API costs.

### Input Guardrails (before the answer)

When using NeMo (default), input guardrails run via Colang 2.x flows in `nemo/main.co`:
1. **PII redaction** — regex-based via custom action (same patterns as built-in)
2. **Stratpoint relevance check** — heuristic keyword matching via custom action; blocks off-topic input
3. **Self-check input** — NeMo LLM-based input moderation
4. **Jailbreak detection** — NeMo heuristic pattern matching

When NeMo is unavailable, the built-in `InputPipeline` runs:
1. **PIIRedactor** — Regex-based detection of:
   - SSNs (`XXX-XX-XXXX`)
   - Credit card numbers (16-digit patterns)
   - Email addresses
   - Phone numbers (international formats)
   All matched entities are replaced with `[REDACTED]` placeholders.

2. **KeywordBlocker** — Regex patterns matching:
   - Prompt injection attempts ("ignore previous instructions")
   - Jailbreak attempts ("DAN", "bypass restrictions")
   - System prompt extraction ("show system prompt")
   - Harmful requests ("how to hack")
   - Attack patterns ("SQL injection")
   Blocked inputs return early with a rejection message.

3. **TopicFilter** — Checks if the input relates to Stratpoint:
   - Heuristic: matches against a comprehensive set of Stratpoint/tech keywords
   - LLM fallback (optional): for ambiguous inputs, queries the NVIDIA NIM to determine relevance
   - The filter is **advisory only** — it does not block inputs, only informs downstream routing

### Output Guardrails (after the answer)

When using NeMo (default), output guardrails run via Colang 2.x flows in `nemo/main.co`:
1. **Output PII redaction** — checks against source docs via custom action; only redacts PII not found in source
2. **Advice blocking** — directive-only patterns for medical, legal, financial advice via custom action
3. **Custom hallucination check** — embedding cosine similarity (threshold 0.75) via custom action
4. **Self-check hallucination** — NeMo LLM-based hallucination detection
5. **Self-check output** — NeMo LLM-based output moderation

When NeMo is unavailable, the built-in `OutputPipeline` runs:

1. **OutputPIIChecker** — Detects PII in the LLM response and **cross-references against source documents**:
   - If PII exists in both the response AND the source text → allowed (it's legitimate content from Stratpoint)
   - If PII appears ONLY in the response → redacted (potential data leak)

2. **HallucinationChecker** — Verifies response grounding:
   - **Primary**: Embedding cosine similarity between the response and source chunks using the same embedder (bge-small-en-v1.5) used for retrieval. Threshold: 0.75.
   - **Fallback**: Optional LLM judge for borderline cases.
   - If similarity is too low, the response is flagged as a potential hallucination.

3. **AdviceBlocker** — Directive-only keyword patterns for:
   - Medical advice ("you should see a doctor")
   - Legal advice ("you should contact a lawyer")
   - Financial advice ("you should invest in this stock", stock picks, market tips)
   - **Source-aware**: if the matched phrase exists in the retrieved source chunks, it's allowed (descriptive Stratpoint content, not generated advice).
   Blocked responses are replaced with a disclaimer redirecting to qualified professionals.

### GuardrailPipeline Composition

The `GuardrailPipeline` class composites input and output checks:
```python
pipeline = GuardrailPipeline(config)
cleaned_input, input_results = pipeline.run_input(user_input)
final_output, output_results = pipeline.run_output(llm_response, source_chunks)
```

Each check returns a `GuardrailResult` with:
- `passed: bool` — whether the check was successful
- `action: "allow" | "block" | "redact" | "escalate"` — what to do
- `message: str` — human-readable explanation
- `modified_input/output: str | None` — sanitized version if applicable

### NeMo Guardrails (Default Backend)

NeMo Guardrails is the **default** guardrail backend. When `nemoguardrails` is installed, all input and output checks run through Colang 2.x flows. The built-in Python pipeline serves as a **graceful fallback** when `nemoguardrails` is not available.

**Architecture:**
- **config.yml**: Points at the same NVIDIA NIM endpoint, model, and API key as the main app (set dynamically from `rag.config.llm_model()` at runtime)
- **main.co**: Colang 2.x flows that orchestrate custom actions alongside NeMo's built-in library rails
- **rails/disallowed.co**: Topic-based disallowed flows for illegal activity, medical, legal, and financial advice (canonical pattern matching)
- **actions.py**: Five custom Python actions that delegate to the same built-in guardrail components — PII redaction, topic relevance, output PII check, hallucination check, and advice blocking

**Custom actions wired in `main.co`:**
```
Input:  PII redact → relevance check → self_check input → jailbreak detection
Output: PII redact → advice check → hallucination check → self_check hallucination → self_check output
```

**How to toggle:**
- `use_nemo=True` (default) in `ChatRequest` or `run_with_guardrails()` — uses NeMo if installed
- `use_nemo=False` — explicitly uses the built-in `GuardrailPipeline`
- Falls back gracefully if `nemoguardrails` is not installed

## Disambiguation Deep-Dive

### Design Philosophy
**Heuristic-first, LLM-fallback classification.** The intent classifier uses rule-based matching for common cases (greetings, harmful content, Stratpoint keywords) and only invokes the LLM when confidence is below 0.7.

### Intent Categories

| Category | Description | Action |
|---|---|---|
| `ask_stratpoint` | Question about Stratpoint services, projects, blog | Proceed to agent retrieval |
| `greeting` | Hello, thanks, pleasantries | Canned greeting response |
| `off_topic` | Completely unrelated topic | Rejection with redirect |
| `harmful` | Prompt injection, malicious requests | Hard rejection |
| `needs_clarification` | Too vague, missing subject | Clarification question |

### Classification Flow

```
User Input
    │
    ├─ Empty? → needs_clarification (0.6)
    ├─ Greeting match? → greeting (0.95)
    ├─ Harmful keyword? → harmful (0.90)
    ├─ Off-topic keyword? → off_topic (0.95)
    ├─ Stratpoint keyword? → ask_stratpoint (0.80)
    ├─ Too short (<5 chars)? → needs_clarification (0.55)
    ├─ Question without keywords? → off_topic (0.60)
    └─ Default → ask_stratpoint (0.50) → LLM fallback
```

If heuristic confidence < 0.7 AND an NVIDIA API key is available, the LLM reclassifies with a structured prompt. The higher-confidence result wins.

### Slot Extraction

For `ask_stratpoint` intents, regex patterns extract:
- **topic**: OutSystems, Flutter, Cloud, UI/UX, Data/AI, DevOps, etc.
- **service_type**: Development, Consulting, Design, Managed Services, Training
- **project_name**: SM Retail App, GCash, StratMega, etc.

Missing required slots trigger a clarification loop (max 3 turns) that asks natural follow-up questions.

## Integration with Existing Code

The guardrails and disambiguation modules are designed as **non-invasive middleware**:

1. **Zero changes** to `agent/agent.py`, `agent/tools.py`, `rag/*`, `prompts/*`, or `ui/*`
2. **Minimal change** to `api/app.py` — swapped `run_agent` → `run_with_guardrails`, added `session_id` and `use_nemo` fields
3. **Same response type** — `AgentResult` is preserved, so the UI/API contract remains identical
4. **Graceful degradation** — all guardrail checks use try/except and default to permissive behavior on error; NeMo falls back to built-in if `nemoguardrails` not installed
5. **NeMo default** — NeMo Guardrails is the default backend; toggle via `use_nemo` flag for explicit fallback

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Guardrail approach | NeMo (default) + custom Python fallback | NeMo provides LLM-powered rails and Colang flows. Falls back to lightweight built-in pipeline when `nemoguardrails` not installed. |
| Classification priority | Heuristic-first, LLM fallback | 90%+ of inputs (greetings, Stratpoint questions, harmful) are caught by regex without a network call |
| PII strategy | Regex patterns, cross-reference sources | Simple, fast, no ML dependency. Cross-referencing prevents false positives on legitimate content |
| Hallucination detection | Embedding cosine similarity | Uses the same embedder as retrieval (bge-small). No extra model downloads. Threshold at 0.75 |
| Memory | Summary buffer (last 6 turns) | Customer service queries are self-contained. LLM summarization adds cost/latency for marginal benefit |
| API key requirement | Only needed for LLM calls | Heuristic guardrails, disambiguation, slot extraction all work offline without any API key |
