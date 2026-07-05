# Mikhos — Self Log

> Module owned: **Disambiguation & Guardrails** — intent classification, slot extraction, multi-turn clarification, input/output guardrails, conversation memory, and agent orchestration.

---

## 2026-07-04 — Designed and scaffolded the Disambiguation & Guardrails pipeline
- Analyzed the full codebase architecture: `rag/` and `prompts/` are built; `disambiguation/`, `guardrails/`, and `agent/` were empty scaffolds.
- Wrote a comprehensive architecture analysis and implementation plan (`stratpoint_rag_prompt.md`) covering all three phases.
- Created branch `feat/disambiguation-guardrails` and ran initial setup: `uv sync`, `stratpoint-rag-ingest` (370 pages indexed), and verified all 49 baseline tests pass.
- Added LangChain dependencies to `pyproject.toml` for `ConversationSummaryBufferMemory`.
- Created the prompt analysis doc and this self-log.

**Produced:**
- `stratpoint_rag_prompt.md` — full architecture analysis, NeMo Guardrails evaluation, and implementation plan.
- `docs/Mikhos_self-log.md` — this file.

## 2026-07-04 — Completed all 3 phases: Disambiguation, Guardrails, and Agent orchestrator
- **Phase 1 (Disambiguation):** Created `schemas.py` (5 Pydantic models + enums), `classifier.py` (LLM-based + heuristic fallback), `slots.py` (slot definitions + LLM extraction), `clarification.py` (multi-turn state machine, max 3 turns), `router.py` (orchestrates classify → clarify → extract → route).
- **Phase 2 (Guardrails):** Created `schemas.py` (config + result models), `input_guardrails.py` (PII redactor with regex, topic filter, keyword blocker), `output_guardrails.py` (PII leak check, combined LLM-judge + semantic hallucination checker, advice blocker), `pipeline.py` (composable pipeline with fail-open/fail-closed), `memory.py` (custom SummaryBuffer with LLM compression + ChromaDB for cross-session persistence).
- **Phase 3 (Agent orchestrator):** Created `agent/orchestrator.py` (`Agent.orchestrate()`: guardrails → disambiguation → retrieve → LLM → output guardrails → memory).
- **Key decisions:** Custom Python over NeMo Guardrails (lighter, more deterministic); heuristic fallback for keyless testing; custom SummaryBuffer (LangChain removed its memory module in 1.3.11).
- **Verification:** All 49 existing tests pass; heuristic classification verified offline for greeting/harmful/question/clarification intents.

**Produced:**
- `src/stratpoint_rag/disambiguation/` — 5 files (schemas, classifier, slots, clarification, router)
- `src/stratpoint_rag/guardrails/` — 6 files (schemas, input, output, pipeline, memory, __init__)
- `src/stratpoint_rag/agent/orchestrator.py` — full production entry point
- `stratpoint_rag_prompt.md` — architecture analysis + NeMo evaluation + implementation plan

**Open:** Wire agent into API/UI subpackages when those are built; set NVIDIA_API_KEY for live testing.
