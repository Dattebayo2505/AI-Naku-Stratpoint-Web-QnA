"""FastAPI app exposing the guarded ReAct agent over HTTP (POST /chat).

Guardrails, disambiguation, and NeMo integration wrap the core ReAct agent
while keeping the same AgentResult response schema the UI expects.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from stratpoint_rag.agent import (
    AgentResult,
    run_with_guardrails,
    stream_with_guardrails,
    warmup,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the process-wide caches (embedder, NeMo rails) in a background thread
    # so the first real request pays warm latency, not the ~137s cold path.
    # /health stays responsive immediately; a request arriving mid-warmup just
    # populates the same lazy singletons. Set WARMUP=0 to skip.
    if os.getenv("WARMUP", "1").lower() not in ("0", "false", "no", ""):
        threading.Thread(target=warmup, kwargs={"use_nemo": True}, daemon=True).start()
    yield


app = FastAPI(title="Stratpoint Support Bot API", lifespan=lifespan)


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
    try:
        return run_with_guardrails(
            req.message,
            history=req.history,
            session_id=req.session_id,
            use_nemo=req.use_nemo,
            enable_reasoning=req.enable_reasoning,
        )
    except RuntimeError as ex:  # config problems (e.g. missing API key)
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # upstream LLM/endpoint failure
        raise HTTPException(status_code=502, detail=f"agent failure: {type(ex).__name__}")


@app.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE stream of the same guarded pipeline. Emits `status`/`delta` events for
    a live preview, then a terminal `done` event carrying the guardrail-safe
    answer + metadata (the authoritative result — clients replace the preview
    with done.answer). One JSON object per `data:` line."""
    def event_gen():
        try:
            for ev in stream_with_guardrails(
                req.message,
                history=req.history,
                session_id=req.session_id,
                use_nemo=req.use_nemo,
            ):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as ex:  # upstream/LLM failure mid-stream
            log.warning("stream failure: %s", ex)
            yield f"data: {json.dumps({'type': 'error', 'detail': f'agent failure: {type(ex).__name__}'})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
