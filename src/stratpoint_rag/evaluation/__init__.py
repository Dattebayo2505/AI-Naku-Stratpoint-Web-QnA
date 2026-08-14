"""Evaluation suite (Component #13).

Layered evals over the production path: guardrails (unit), trajectory, end-to-end
and LLM-as-judge, plus the existing retrieval and prompt-ablation evals. Run all
of them with `uv run python -m stratpoint_rag.evaluation`. The harness REGISTRY
is the single extension seam.
"""
