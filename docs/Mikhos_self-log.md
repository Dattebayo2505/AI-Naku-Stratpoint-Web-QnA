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
- `src/stratpoint_rag/guardrails/` — 6 files (schemas, input, output, pipeline, memory, **init**)
- `src/stratpoint_rag/agent/orchestrator.py` — full production entry point
- `stratpoint_rag_prompt.md` — architecture analysis + NeMo evaluation + implementation plan

**Open:** Wire agent into API/UI subpackages when those are built; set NVIDIA_API_KEY for live testing.

## 2026-07-05 — Integrated NVIDIA NeMo Guardrails as an alternative backend

- Created branch `feat/nemo-guardrails` and installed `nemoguardrails>=0.17.0` (resolved to v0.23.0).
- Discovered NeMo v0.23.0 uses a different API: `config.yml` + `.co` Colang files (colang v2.x), `flow main` required, `RailsConfig.from_path()` for config loading, and `RailType.INPUT`/`.OUTPUT` enums for `check()`.
- Built NeMo config directory (`guardrails/nemo/`) with `config.yml` (NVIDIA NIM via `engine: openai` with `base_url`), `main.co` (imports library flows + defines `flow main` + input/output rails), `actions.py` (custom actions for PII, topic, hallucination, advice via `@action` decorator), and `rails/disallowed.co` (topic-based disallowed flows).
- Built `guardrails/nemo_guardrails.py` — `NeMoGuardrailPipeline` wrapper matching the same `run_input()`/`run_output()` interface as `GuardrailPipeline`, with graceful fallback when no API key is available.
- Updated `agent/orchestrator.py` to accept `use_nemo=True/False` toggle.
- Verified: `RailsConfig.from_path()` loads correctly, `LLMRails` initializes and calls library flows, `Agent(use_nemo=True)` creates and runs with NeMo backend. Graceful fallback works without API key (logs error, allows through).
- All 49 existing unit tests pass (no regressions).

**Produced:**

- `src/stratpoint_rag/guardrails/nemo/config.yml` — NeMo model config with NVIDIA NIM endpoint
- `src/stratpoint_rag/guardrails/nemo/main.co` — Colang flows: main, input rails, output rails
- `src/stratpoint_rag/guardrails/nemo/actions.py` — Custom Python actions for PII, topic, hallucination, advice
- `src/stratpoint_rag/guardrails/nemo/rails/disallowed.co` — Topic-based disallowed flows
- `src/stratpoint_rag/guardrails/nemo_guardrails.py` — NeMo wrapper with graceful fallback
- `docs/test-results.md` — updated with NeMo test outcomes
- `docs/architecture-flow.md` — updated with NeMo integration details and file reference

**Key decisions:** Using `engine: openai` with `base_url` instead of `engine: nvidia_nim` (NeMo's `nvidia_nim` requires provider not present in v0.23.0); defining `flow main` with `activate llm continuation` is mandatory for colang v2.x; importing library flows via module paths (`nemoguardrails.library.self_check.input_check`, etc.) with spaces in flow names.

**Open:** NeMo requires LLM calls even for heuristic-only rails — API key needed for non-fallback mode. Evaluate if NeMo's library flows (jailbreak detection, self-check, hallucination) provide enough additional value vs. our custom pipeline, which operates fully offline. Wire into API/UI subpackages.
