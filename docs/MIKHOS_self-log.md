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

*   **Documentation**:
    *   Created `docs/architecture-flow.md` with the full system architecture showing the end-to-end flow, module responsibilities, and explanation for the panel defense.
    *   Updated the classification flow diagram to include the off-topic keyword pre-filter step.
    *   Created this self-log.
