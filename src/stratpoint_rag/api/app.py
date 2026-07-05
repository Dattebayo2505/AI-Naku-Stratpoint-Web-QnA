"""FastAPI app exposing the ReAct agent over HTTP (POST /chat)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stratpoint_rag.agent import AgentResult, run_agent

app = FastAPI(title="Stratpoint Support Bot API")


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None
    session_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=AgentResult)
def chat(req: ChatRequest) -> AgentResult:
    try:
        return run_agent(req.message, history=req.history)
    except RuntimeError as ex:  # config problems (e.g. missing API key)
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # upstream LLM/endpoint failure
        raise HTTPException(status_code=502, detail=f"agent failure: {type(ex).__name__}")
