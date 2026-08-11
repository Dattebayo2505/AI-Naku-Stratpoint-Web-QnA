# Keisha — Self Log

My personal working log for the Stratpoint RAG Chatbot (STAI100 Midterm Capstone).
Module I own: **Prompt Engineering** (system prompts, few-shot examples, Chain-of-Thought, structured output schemas, and ablation studies).

---

## 2026-07-04
*   **Prompt Engineering Module Implementation**:
    *   Designed and created the `stratpoint_rag/prompts/` package to version and control prompt variants.
    *   Created `schema.py` containing Pydantic models for structured answers (`GroundedAnswer`, `Citation`) ensuring responses output clean, validated JSON.
    *   Created `few_shot_examples.py` with 3 curated QA examples (fully grounded, partially grounded, and out-of-scope refusal) to teach the model how to reason and refuse hallucinations.
    *   Created `system_prompts.py` containing V0 (zero-shot), V1 (few-shot), V2 (CoT), V3 (role), and V4 (combined) prompts.
    *   Created `builder.py` and `registry.py` to compile prompts consistently.
*   **Conducted Ablation Study**:
    *   Created `run_ablation.py` to evaluate 6 variant-temperature configurations against a fixed set of 7 test questions (5 gold search hits + 2 out-of-scope/unanswerable queries).
    *   Ran the ablation study sequentially against the NVIDIA NIM cloud endpoint (`google/gemma-4-31b-it`), achieving **100% JSON schema validity** across all structured runs.
    *   Saved results in `evaluation/prompt_ablation_results.jsonl` and summarized findings in `evaluation/PROMPT_ENGINEERING_FINDINGS.md`.
    *   Identified **`v4_combined_lowtemp`** ($T=0.1$) as the winning prompt variant due to its perfect JSON adherence and highest precision in refusing out-of-scope queries (85.71% accuracy).
*   **Integrated Winning Variant**:
    *   Refactored `src/stratpoint_rag/rag/answer.py` to use the winning `v4_combined_lowtemp` system prompt, parse response JSON, and format citations consistently.
    *   Added auto `.env` loading and blank-value fallback handlers in `src/stratpoint_rag/rag/config.py`.
    *   Ignored `chroma_db/` in `.gitignore` to prevent database binary leaks.
*   **Verification**:
    *   Verified the answering pipeline returns correct answers and clean citations end-to-end, and verified that all 49 existing crawler tests remain green.

## 2026-07-05
*   **Chat UI Module Implementation**:
    *   Designed and built the Streamlit frontend (`stratpoint_rag/ui/`) to act as the primary interface for the capstone demo.
    *   Created `api_client.py` as a robust HTTP wrapper to communicate with the FastAPI backend, complete with timeout handling and error catching.
    *   Implemented session memory management in `state.py` to maintain multi-turn context via unique session IDs.
    *   Built an "Under the hood" debug panel (`debug_panel.py`) for every assistant turn that surfaces retrieved citations, the agent's ReAct trace (thoughts/actions/observations), grounding/refusal status, and the raw JSON response payload.
    *   Used defensive `.get()` programming to ensure the UI gracefully degrades if optional backend modules (like guardrails) are missing.
    *   Resolved a dependency issue by adding `streamlit` and `requests` to the project's `pyproject.toml` via `uv add` and testing the system end-to-end.

## 2026-07-07 to 2026-07-09
*   **Guardrails & Routing Logic Integration**:
    *   Integrated multi-layer guardrails combining NeMo Guardrails with fast keyword pre-filtering to catch harmful or out-of-scope queries before LLM execution.
    *   Updated default routing logic to direct general questions straight to `ASK_STRATPOINT` and skip unnecessary clarification loops.
    *   Remediated F1-F3 testing handoff defects, fixed PDF link matching regex, and added configurable LLM timeouts.
    *   Preserved markdown link integrity during document chunk splitting.
*   **Prompt Schema Refinements**:
    *   Removed explicit reasoning field from `GroundedAnswer` schema to support native LLM reasoning traces.
    *   Added dedicated prompt ablation test suite for automated prompt regression evaluations.

## 2026-07-27
*   **LLM Cutover to Llama-3.1-8B-Instruct**:
    *   Switched default backend model from `google/gemma-4-31b-it` to `meta/llama-3.1-8b-instruct`.
    *   Rebuilt assistant architecture to be provider-portable and decoupled from heavy agent framework dependencies.
*   **Plain-Text ReAct Agent Architecture**:
    *   Designed and implemented custom plain-text ReAct parser and execution loop (`src/stratpoint_rag/agent/react.py`) with automatic reprompting, tool registry (`TOOL_SPECS`), and RAG fallback.
    *   Implemented `anchor_entity` query rewriter to convert ambiguous pronouns into explicit entity references.
*   **Retrieval Evaluation Framework**:
    *   Built evaluation CLI with gold-case schema validation, rank capture, hit-rate by axis, MRR calculations, and baseline diffing.
    *   Established corpus fingerprint baseline with separation reports to gate retrieval quality.

## 2026-08-03
*   **LLMOps & Observability**:
    *   Implemented JSONL trace logging sink for agent turns and added a `/metrics` API endpoint for real-time operational monitoring.

## 2026-08-07 to 2026-08-09
*   **Document Parsing Engine (`docparse`)**:
    *   Built two-hop document processing pipeline for uploaded RFPs and client briefs:
        *   **Hop-1 (Transcription)**: Switched vision backend to `nvidia/nemotron-nano-12b-v2-vl` NIM model for document OCR, table reconstruction, and markdown rendering.
        *   **Hop-2 (Structured Extraction)**: Extracted structured scope metadata (`EstimationInput`, features, target platforms) from client documents.
    *   Created `read_brief` tool allowing the agent to query uploaded brief context (capped at 40 chunks max).
*   **Proposal Generator & Currency Calculator**:
    *   Developed currency calculator supporting PHP and USD conversions with configurable exchange rates (`EXCHANGE_RATE_PESOS_PER_DOLLAR`).
    *   Built HTML proposal generator and PDF export pipeline (`pdf_gen`) with custom line item formatting (`license` vs `hrs`), role breakdown, and payment schedules.
    *   Added background document processing dialog to Streamlit UI for seamless asynchronous uploads.

## 2026-08-10 to 2026-08-11
*   **Category Costing & Pricing Handbook Rules**:
    *   Added `get_category_costings()` in `currency_calculator.py` to automatically detect Cloud/DevOps, AI/ML, Data Engineering, Security, and Software License categories from extracted brief features.
    *   Integrated category-specific handbook rates (e.g. Senior AI/ML @ ₱3,625/hr, Senior DevOps @ ₱2,610/hr) and annual licenses (Gemini Enterprise, Cloud Storage, Google Workspace) directly into `estimate_cost_and_timeline()`.
    *   Streamlined Streamlit sidebar layout, restored reset conversation controls, and added unit tests (`test_category_costing.py`).