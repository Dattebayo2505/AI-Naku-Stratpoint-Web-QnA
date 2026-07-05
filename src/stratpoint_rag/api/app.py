"""FastAPI app exposing the guarded ReAct agent over HTTP (POST /chat).

Guardrails, disambiguation, and NeMo integration wrap the core ReAct agent
while keeping the same AgentResult response schema the UI expects.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stratpoint_rag.agent import AgentResult, run_with_guardrails

app = FastAPI(title="Stratpoint Support Bot API")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None
    session_id: str | None = None
    use_nemo: bool = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=AgentResult)
def chat(req: ChatRequest) -> AgentResult:
    try:
        return run_with_guardrails(
            req.message,
            history=req.history,
            session_id=req.session_id,
            use_nemo=req.use_nemo,
        )
    except RuntimeError as ex:  # config problems (e.g. missing API key)
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # upstream LLM/endpoint failure
        raise HTTPException(status_code=502, detail=f"agent failure: {type(ex).__name__}")
