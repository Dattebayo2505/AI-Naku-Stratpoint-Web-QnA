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
 ┌──────────────────────────┐   ┌─────────────────────┐
 │  GuardrailPipeline       │   │  NeMoGuardrail      │
 │  (pipeline.py)           │   │  Pipeline            │
 │                          │   │  (when use_nemo)     │
 │  InputPipeline:          │   │  (nemo_guardrails    │
 │  • KeywordBlocker        │   │   .py → main.co)    │
 │  • PIIRedactor           │   │                     │
 │  • TopicFilter           │   │  Colang 2.x flows:  │
 │                          │   │  • PII redact       │
 │                          │   │  • Relevance check  │
 │                          │   │  • Self-check input │
 │                          │   │  • Jailbreak detect │
 └──────────┬───────────────┘   └──────────┬──────────┘
            │                              │
            │  Built-in keyword/PII checks │
            │  run first (fast, zero API   │
            │  cost). NeMo runs second as  │
            │  an LLM-powered safety net   │
            │  for nuanced cases.          │
            │                              │
            └──────────┬───────────────────┘
                       ▼ cleaned input
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
│  │  • Output PII check (cross-reference source)                        │    │
│  │  • Advice blocker (medical/legal/financial directive patterns)       │    │
│  │  • Hallucination checker (embedding cosine sim)                     │    │
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

**Two-layer architecture**: Input guardrails always run built-in checks first, then NeMo (if enabled). The built-in `KeywordBlocker` and `PIIRedactor` run first — they catch obvious injection patterns in microseconds with zero API cost. If those pass, NeMo's Colang 2.x flows run as a more thorough LLM-powered second layer — catching nuanced cases regex patterns might miss (e.g., a creatively phrased social engineering attempt). The `TopicFilter` is intentionally skipped during the built-in pass because the disambiguation classifier handles relevance downstream — avoids an unnecessary LLM call per query.

When NeMo is unavailable (not installed), only the built-in `GuardrailPipeline` runs: `KeywordBlocker`, `PIIRedactor`, and `TopicFilter`.

#### Built-in Pass (always runs first)
Runs fast regex checks before any LLM call:
1. **KeywordBlocker** — Regex blocking for:
   - Prompt injection ("ignore previous instructions")
   - Jailbreak attempts ("DAN", "bypass")
   - Harmful content ("hack", "exploit", "malware", "ransomware", "DDoS", "crack password")
   - Attack patterns ("SQL injection", "XSS")

2. **PIIRedactor** — SSN, credit card, email, phone redaction

#### NeMo Input Flow (runs second, if enabled)
If the built-in checks pass and `use_nemo=True`, NeMo's Colang flows run as a more thorough second pass. Defined in `nemo/main.co`:
1. **PII redaction** — custom action (same regex patterns as built-in)
2. **Stratpoint relevance check** — custom action keyword matching
3. **Self-check input** — NeMo LLM-based input moderation
4. **Jailbreak detection** — NeMo heuristic + LLM pattern matching

When a NeMo rail fires (e.g., jailbreak detected), it appends an assistant message to the response. The wrapper (`nemo_guardrails.py`) detects this by checking for extra messages in the result, not just exceptions — making NeMo's built-in rails actually effective.

#### Built-in Input Pipeline (NeMo unavailable)
Runs `InputPipeline` with all three checks:
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

Output guardrails always run built-in checks first, then NeMo (if enabled).

#### Built-in Output Pipeline (always runs first)
Fast, no extra API cost:
1. **OutputPIIChecker** — Detects PII in the LLM response and **cross-references against source documents**:
   - If PII exists in both the response AND the source text → allowed (it's legitimate content from Stratpoint)
   - If PII appears ONLY in the response → redacted (potential data leak)

2. **HallucinationChecker** — Verifies response grounding:
   - **Primary**: Embedding cosine similarity between the response and source chunks using the same embedder (bge-small-en-v1.5) used for retrieval. Threshold: 0.6.
   - **Fallback**: Optional LLM judge for borderline cases.
   - If similarity is too low, the response is flagged as a potential hallucination.

3. **AdviceBlocker** — Directive-only, source-aware keyword patterns for:
   - Medical advice ("you should see a doctor")
   - Legal advice ("you should contact a lawyer")
   - Financial advice ("you should invest in this stock", stock picks, market tips)
   - **Source-aware**: if the matched phrase exists in the retrieved source chunks, it's allowed (descriptive Stratpoint content, not generated advice). The blocker only matches directive language (e.g. "you should", "I recommend") — descriptive text from the corpus that happens to mention medical/legal/financial topics is not blocked.
   Blocked responses are replaced with a disclaimer redirecting to qualified professionals.

#### NeMo Output Flow (runs second, if enabled)
If built-in checks pass and `use_nemo=True`, NeMo's Colang flows run as a more thorough second pass:
1. **Output PII redaction** — checks against source docs via custom action; only redacts PII not found in source
2. **Advice blocking** — directive-only, source-aware patterns for medical, legal, financial advice via custom action
3. **Custom hallucination check** — embedding cosine similarity (threshold 0.6) via custom action

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

### NeMo Guardrails (LLM-Powered Second Layer)

NeMo Guardrails is an **optional LLM-powered second layer** on top of the built-in pipeline. When `nemoguardrails` is installed, NeMo's Colang 2.x flows run **after** the built-in keyword/PII checks. The built-in pipeline always runs first and handles the fast regex checks; NeMo catches nuanced cases the regex might miss.

When `nemoguardrails` is not available, only the built-in `GuardrailPipeline` runs — no functionality is lost, just the extra LLM-powered layer.

**Architecture:**
- **config.yml**: Points at the same NVIDIA NIM endpoint, model, and API key as the main app (set dynamically from `rag.config.llm_model()` at runtime)
- **main.co**: Colang 2.x flows that orchestrate custom actions alongside NeMo's built-in library rails
- **rails/disallowed.co**: Topic-based disallowed flows for illegal activity, medical, legal, and financial advice (canonical pattern matching)
- **actions.py**: Five custom Python actions that delegate to the same built-in guardrail components — PII redaction, topic relevance, output PII check, hallucination check, and advice blocking

**Custom actions wired in `main.co`:**
```
Input:  PII redact → relevance check → self_check input → jailbreak detection
Output: PII redact → advice check (source-aware) → hallucination check (cosine sim threshold 0.6)
```

**How to toggle:**
- `use_nemo=True` (default) in `ChatRequest` or `run_with_guardrails()` — uses built-in first, then NeMo as a second pass
- `use_nemo=False` — only uses the built-in `GuardrailPipeline`
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
    ├─ Question (has ? or starts with question word)? → ask_stratpoint (0.70)
    └─ Default → ask_stratpoint (0.50) → LLM fallback
```

The heuristic only invokes the LLM fallback when confidence < 0.7 AND the NVIDIA API key is available. Since questions now default to 0.7, the LLM fallback is almost never triggered for questions — the RAG pipeline handles ambiguity downstream, and the grounded-answer prompt naturally says "I don't know" when the corpus has no relevant content.

**Router confidence demotion**: The router previously demoted any query with confidence < 0.7 to `NEEDS_CLARIFICATION`. Now it skips demotion for structured questions (has `?` or starts with a question word). Only short/shapeless inputs without question structure go to clarification — this means queries like "Where are you located?" proceed directly to answer even without an explicit Stratpoint keyword match.

**Note**: The classifier's harmful check is the third defensive layer — NeMo and the KeywordBlocker should catch most harmful inputs before they reach the classifier. The classifier keyword set serves as a final regex safety net.

### Slot Extraction

For `ask_stratpoint` intents, regex patterns extract:
- **topic**: OutSystems, Flutter, Cloud, UI/UX, Data/AI, DevOps, etc.
- **service_type**: Development, Consulting, Design, Managed Services, Training
- **project_name**: SM Retail App, GCash, StratMega, etc.

Missing required slots trigger a clarification loop (max 3 turns) that asks natural follow-up questions.

When a `matched_keyword` is captured during slot extraction, it's threaded through the `RouteResult` and down to `guardrail_agent.py` — the answer phase uses it to decide on retrieval strategy. For Contact/Location queries (matched via `contact|email|phone|address|locat|office|reach|find`), the agent augments retrieval by querying Chroma with `where_document={"$contains": "office"}` and expanding the query with matched slug names — ensuring the workspace/office pages surface in top results without hardcoded content.

## Integration with Existing Code

The guardrails and disambiguation modules are designed as **non-invasive middleware**:

1. **Zero changes** to `agent/agent.py`, `agent/tools.py`, `rag/*`, `prompts/*`, or `ui/*`
2. **Minimal change** to `api/app.py` — swapped `run_agent` → `run_with_guardrails`, added `session_id` and `use_nemo` fields
3. **Same response type** — `AgentResult` is preserved, so the UI/API contract remains identical
4. **Graceful degradation** — all guardrail checks use try/except and default to permissive behavior on error; NeMo falls back to built-in if `nemoguardrails` not installed
5. **Built-in first, NeMo second** — fast regex checks run first (zero API cost); NeMo runs as an LLM-powered second pass for nuanced cases; toggle via `use_nemo` flag

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Guardrail order | Built-in (fast regex) first → NeMo (LLM-powered) second | Built-in catches obvious patterns in microseconds with zero API cost. NeMo catches nuanced cases the regex might miss. Neither is a single point of failure. |
| Classification priority | Heuristic-first, LLM fallback | 90%+ of inputs (greetings, Stratpoint questions, harmful) are caught by regex without a network call |
| PII strategy | Regex patterns, cross-reference sources, `allowed_email_domains` allowlist | Simple, fast, no ML dependency. Cross-referencing prevents false positives on legitimate content. `@stratpoint.com` emails exempted from redaction |
| Hallucination detection | Embedding cosine similarity | Uses the same embedder as retrieval (bge-small). No extra model downloads. Threshold at 0.6 |
| Memory | Summary buffer (last 6 turns) | Customer service queries are self-contained. LLM summarization adds cost/latency for marginal benefit |
| API key requirement | Only needed for LLM calls | Heuristic guardrails, disambiguation, slot extraction all work offline without any API key |
