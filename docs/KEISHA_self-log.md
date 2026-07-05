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