# Mikhos — Self Log

My personal working log for the Stratpoint RAG Chatbot (STAI100 Midterm Capstone).
Modules I own: **Guardrails, Disambiguation, NeMo Integration, and Architecture Documentation**.

---

## 2026-07-05

*   **Guardrails Module Implementation**:
    *   Designed and built the `stratpoint_rag/guardrails/` package as a composable safety layer around the chatbot pipeline.
    *   Created `schemas.py` with Pydantic models (`GuardrailConfig`, `GuardrailResult`, `RedactionRule`) defining the contract between guardrail components.
    *   Created `input_guardrails.py`: `PIIRedactor` (regex-based SSN, credit card, email, phone redaction), `KeywordBlocker` (patterns for prompt injection, jailbreak, info leak attempts), and `TopicFilter` (heuristic keyword matching with optional LLM fallback for ambiguous inputs).
    *   Created `output_guardrails.py`: `OutputPIIChecker` (leak detection that distinguishes PII in output vs source documents), `HallucinationChecker` (embedding cosine similarity between response and source chunks with optional LLM judge), and `AdviceBlocker` (patterns for medical, legal, and financial advice prevention).
    *   Created `memory.py` with `ConversationMemory` — a circular buffer holding the last 6 exchanges per session for conversational context, using zero LLM calls.
    *   Created `pipeline.py` with `GuardrailPipeline` composing input and output checks into a single `run_input() → run_output()` interface.

*   **Disambiguation Module Implementation**:
    *   Designed and built the `stratpoint_rag/disambiguation/` package to classify user intent and resolve ambiguity before committing to retrieval.
    *   Created `schemas.py` with `IntentCategory` (5 categories), `RouteResult`, and `ClarificationSession` models.
    *   Created `classifier.py` — heuristic-first intent classification using a greeting set, keyword matching for Stratpoint-related terms, harmful pattern detection, with LLM fallback only when heuristic confidence falls below 0.7.
    *   Created `slots.py` — regex-based slot extraction for known Stratpoint entities (OutSystems, Flutter, AWS, services, projects, pricing) without any LLM calls.
    *   Created `clarification.py` — multi-turn clarification loop (max 3 turns) that generates natural follow-up questions when slots are missing.
    *   Created `router.py` — orchestrates classifier → slots → clarification flow, routing to retrieval, greeting response, rejection, or clarification.

*   **Integration Wrapper**:
    *   Created `agent/guardrail_agent.py` — `run_with_guardrails()` that wraps the existing LangGraph ReAct agent with guardrails and disambiguation. The wrapper runs: input guardrails → disambiguation → existing `run_agent()` (if ASK_STRATPOINT) → output guardrails → memory update. Returns the same `AgentResult` type so the API and UI work without changes.
    *   Exported `run_with_guardrails` from `agent/__init__.py` alongside the existing `run_agent`.

*   **API Wiring**:
    *   Updated `api/app.py` to call `run_with_guardrails` instead of `run_agent`, threading `session_id` and the new `use_nemo` toggle through the request handler. No response schema changes — the UI remains unaffected.

*   **NeMo Guardrails Integration**:
    *   Restored NeMo configuration files from the previous branch: `guardrails/nemo/config.yml` (NVIDIA NIM model config), `main.co` (Colang 2.x flows with self-check input, jailbreak detection, hallucination check, output check), `rails/disallowed.co` (topic-based disallowed flows for illegal activity, medical, legal, financial advice), and `actions.py` (custom actions for PII redaction, Stratpoint relevance, output PII check, hallucination check, advice blocking that delegate to the built-in guardrail components).
    *   Created `guardrails/nemo_guardrails.py` — `NeMoGuardrailPipeline` matching the same `run_input()`/`run_output()` interface as `GuardrailPipeline`, making it a drop-in replacement. Graceful fallback when `nemoguardrails` is not installed.
    *   Added `[project.optional-dependencies] nemo` to `pyproject.toml` for optional installation.

*   **Fast off-topic rejection**:
    *   Added `_OFF_TOPIC_KEYWORDS` set (medical, weather, sports, entertainment, cooking, travel, politics, crypto, homework) to `classifier.py` as a pre-filter between the harmful check and the Stratpoint keyword check. Queries like "I have a fever" now return `OFF_TOPIC` at 0.95 confidence without any LLM call — no unnecessary model cost for clearly irrelevant input.

*   **Slot extraction fix — "what is stratpoint" routing**:
    *   Fixed the general-topic slot pattern in `slots.py` from `what.*do` to `what\s+(?:is|are|does|do)` so that "What is Stratpoint?", "What are your services", etc. match the "General" topic slot. Previously these queries fell through to the clarification loop ("I'm not sure I understand...") because `what.*do` required the word "do" after "what". Now they route directly to the RAG answer path.

## 2026-07-07

*   **NeMo input rail detection fix**:
    *   Fixed `nemo_guardrails.py:run_input()` and `run_output()` to properly check NeMo's actual response content for blocking signals, not just exceptions. When NeMo's jailbreak detection or self-check input fires, it inserts an assistant message into the response — the wrapper now detects this and treats it as a block. Previously, NeMo's built-in rails (jailbreak detection, self-check input) were effectively silent because only `result.exception` was checked.

*   **Multi-layer guardrail architecture**:
    *   Restructured `guardrail_agent.py` to run NeMo first, then supplement with the built-in KeywordBlocker and PIIRedactor as a secondary pass. The TopicFilter is skipped in the supplement because the disambiguation classifier handles relevance downstream — avoids an unnecessary LLM call per query. This means NeMo handles LLM-level safety (jailbreak, self-check) while the built-in regex layer catches patterns NeMo might miss.

*   **Broadened harmful pattern coverage**:
    *   Added `\bhack\b`, `\bexploit\b`, `\bmalware\b`, `\bransomware\b`, `\bcrack\s+(software|password|account|system|code)\b`, `\bDDoS\b` to `BLOCKED_PATTERNS` in `input_guardrails.py` — catches "help me hack into your system" without requiring "how to" prefix.
    *   Added `"help me hack"`, `"malware"`, `"ransomware"`, `"trojan"`, `"virus"`, `"ddos"`, `"sql injection"`, `"xss"` to `_HARMFUL_KEYWORDS` in `classifier.py` for broader safety net in the disambiguation layer.

*   **`guardrail_reason` always populated**:
    *   `guardrail_reason` is now set on ALL non-standard return paths (greeting, clarification, blocked) in `guardrail_agent.py` — the debug panel always shows why a response was routed the way it was.
    *   Greeting: `"Greeting detected"`
    *   Clarification: `"Needed clarification: {intent}"`
    *   Blocked: the blocking reason from the guardrail

*   **Edge-case "what is stratpoint" routing**:
    *   Fixed the general-topic slot pattern in `slots.py` from `what.*do` to `what\s+(?:is|are|does|do)` so that "What is Stratpoint?" matches the "General" topic slot.

*   **Conversational guardrail blocks**:
    *   Added `_user_facing_block()` to `guardrail_agent.py` that converts internal block reasons (e.g. `"Blocked: matched 'harmful_request'"`) into conversational user-facing messages. The technical reason stays in `guardrail_reason` for the debug panel, while the user sees friendly text: `"I'm sorry, I can't help with that."` for harmful content and `"I can only answer questions about Stratpoint."` for injection/jailbreak attempts.

*   **Broader Stratpoint keyword matching for general questions**:
    *   Added `"what do you do"`, `"what do you"`, `"who are you"`, `"tell me about yourself"`, `"what is this"`, `"what is stratpoint"` to `_STRATPOINT_KEYWORDS` in `classifier.py` so that company-about queries without explicit "Stratpoint" mention route directly to ASK_STRATPOINT instead of falling through to the clarification loop.
    *   Added `"where are you"`, `"where is"`, `"how do i"`, `"contact"`, `"location"`, `"address"`, `"phone"`, `"email"` to `_STRATPOINT_KEYWORDS` so that contact/location queries (e.g. "Where are you located?") are classified as Stratpoint questions.
    *   Added `"Contact / Location"` topic pattern to `_TOPIC_PATTERNS` in `slots.py` matching `contact|email|phone|address|locat|office|reach|find` — so "How do I contact Stratpoint?" extracts `topic` successfully and skips the clarification loop.

*   **Default all questions to ASK_STRATPOINT — no more keyword whack-a-mole**:
    *   Changed the default for any question without a Stratpoint keyword from `OFF_TOPIC (0.6)` to `ASK_STRATPOINT (0.7)`. The off-topic keyword pre-filter (fever, weather, sports) still catches clearly irrelevant stuff at 0.95 before reaching this point. Everything else goes to RAG — if the corpus has nothing relevant, the grounded-answer LLM naturally says "I don't know."
    *   Updated the router to skip confidence demotion for structured questions (has `?` or starts with a question word). Short or shapeless inputs still go to clarification.

*   **Configurable LLM timeout**:
    *   Added `LLM_TIMEOUT` env var (default 300s) to `rag/config.py` and `.envexample`. All four external LLM call sites (`answer_grounded`, classifier LLM fallback, TopicFilter LLM check, hallucination LLM judge) now use it instead of hardcoded 15s/30s/120s values.

*   **Documentation**:
    *   Updated this self-log.

---
