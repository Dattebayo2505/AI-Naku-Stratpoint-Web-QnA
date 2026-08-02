"""FastAPI app exposing the guarded ReAct agent over HTTP (POST /chat).

Guardrails, disambiguation, and NeMo integration wrap the core ReAct agent
while keeping the same AgentResult response schema the UI expects. Every /chat
turn is recorded to the LLMOps trace sink (Component #8); GET /metrics serves
the aggregates.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stratpoint_rag import llmops
from stratpoint_rag.agent import AgentResult, run_with_guardrails
from stratpoint_rag.rag import config as rag_config

app = FastAPI(title="Stratpoint Support Bot API")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None
    session_id: str | None = None
    use_nemo: bool = True
    enable_reasoning: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=AgentResult)
def chat(req: ChatRequest) -> AgentResult:
    t = time.perf_counter()
    llmops.reset_usage()  # scope the token accumulator to this request
    try:
        result = run_with_guardrails(
            req.message,
            history=req.history,
            session_id=req.session_id,
            use_nemo=req.use_nemo,
            enable_reasoning=req.enable_reasoning,
        )
    except RuntimeError as ex:  # config problems (e.g. missing API key)
        _record("/chat", t, req, error="RuntimeError")
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # upstream LLM/endpoint failure
        _record("/chat", t, req, error=type(ex).__name__)
        raise HTTPException(status_code=502, detail=f"agent failure: {type(ex).__name__}")

    _record("/chat", t, req, result=result)
    return result


@app.get("/metrics")
def metrics() -> dict:
    """LLMOps view: aggregate metrics + most-recent records (newest first)."""
    recs = llmops.read_records()
    return {"aggregates": llmops.aggregate(recs), "recent": recs[-50:][::-1]}


def _record(
    path: str,
    t_start: float,
    req: ChatRequest,
    *,
    result: AgentResult | None = None,
    error: str | None = None,
) -> None:
    usage = llmops.pop_usage() or {}
    llmops.record(
        path,
        (time.perf_counter() - t_start) * 1000,
        error=error,
        session_id=req.session_id,
        model=rag_config.llm_model(),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        tool_calls=[s.tool for s in result.trace if s.type == "action" and s.tool] if result else None,
        is_grounded=result.is_grounded if result else None,
        confidence=result.confidence if result else None,
    )
