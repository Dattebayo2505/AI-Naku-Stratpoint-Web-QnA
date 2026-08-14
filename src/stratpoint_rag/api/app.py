"""FastAPI app exposing the guarded ReAct agent over HTTP (POST /chat).

Guardrails, disambiguation, and NeMo integration wrap the core ReAct agent
while keeping the same AgentResult response schema the UI expects. Every /chat
turn is recorded to the LLMOps trace sink (Component #8); GET /metrics serves
the aggregates.

Uploaded client briefs enter through POST /upload and are transcribed by
POST /upload/{id}/parse. Upload is split from parse deliberately: you cannot
show a page count before opening the file, so the UI's confirmation dialog
needs a cheap sub-second call first, and PyMuPDF stays out of the UI process.

**Ids, never paths.** The alternative — the UI writing to a shared directory
and putting the path in the chat message — makes the file path LLM-generated
free text flowing into open(). The guardrails package guards the user's
*message*, not tool arguments, so `../../.env` would be one prompt injection
away. It also silently couples UI and API to one filesystem, breaking the
moment anyone runs the UI locally against the LXC API, which STRATPOINT_API_URL
explicitly supports. The model never sees or types a filesystem path.
"""
from __future__ import annotations

import time

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from stratpoint_rag import llmops
from stratpoint_rag.agent import AgentResult, run_with_guardrails
from stratpoint_rag.docparse import (
    EncryptedDocument,
    UnsupportedDocument,
    render,
    store,
    transcribe_document,
)
from stratpoint_rag.docparse import config as docparse_config
from stratpoint_rag.pdf_gen import store as proposal_store
from stratpoint_rag.rag import config as rag_config

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Purge uploads and proposals on boot — the real 'delete on next run',
    keyed to the process that owns the files.

    Streamlit re-executes ui/app.py on every widget interaction, so keying
    cleanup to script execution would delete the file the user just uploaded.
    The files live here, in a long-lived uvicorn that may not restart for days,
    and Streamlit offers no reliable tab-close callback — so boot is the one
    moment we can be certain an old session is over.

    Proposals go with them: a quote carries a client's name and their price, and
    it is derived from a brief this same hook is deleting. Keeping the derived
    document after purging its source would be the leak the purge exists to
    prevent, one step removed.
    """
    store.purge_all()
    proposal_store.purge_all()
    yield


app = FastAPI(title="Stratpoint Support Bot API", lifespan=_lifespan)


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None
    session_id: str | None = None
    use_nemo: bool = True
    enable_reasoning: bool = False
    # Upload ids, not paths. A list so multi-file generalizes later; merge
    # semantics ("whose constraints win?") are deliberately not built yet.
    #
    # Resolved against `session_id` before it reaches the agent: an id that has
    # been swept, deleted, or belongs to another session simply is not in the
    # resulting brief list, so the loop is never offered a tool it cannot run.
    attachments: list[str] | None = None


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    pages: int
    sha256: str
    bytes: int
    cached: bool = False


class ParseResponse(BaseModel):
    upload_id: str
    markdown_path: str
    pages_total: int
    pages_parsed: int
    pages_failed: list[int]
    pages_via_vision: int
    truncated: bool


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=AgentResult)
def chat(req: ChatRequest) -> AgentResult:
    t = time.perf_counter()
    llmops.reset_usage()  # scope the token accumulator to this request
    # Ids -> resolved briefs happens here, at the boundary that knows the
    # session. The agent never sees an id it has not already had checked, and
    # never sees a path at all.
    briefs = store.resolve_briefs(req.session_id or "", req.attachments)
    try:
        result = run_with_guardrails(
            req.message,
            history=req.history,
            session_id=req.session_id,
            use_nemo=req.use_nemo,
            enable_reasoning=req.enable_reasoning,
            briefs=briefs,
        )
    except RuntimeError as ex:  # config problems (e.g. missing API key)
        _record_chat(t, req, error="RuntimeError")
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # upstream LLM/endpoint failure
        _record_chat(t, req, error=type(ex).__name__)
        raise HTTPException(status_code=502, detail=f"agent failure: {type(ex).__name__}")

    _record_chat(t, req, result=result)
    return result


# ── uploads ─────────────────────────────────────────────────────────────────


@app.post("/upload", response_model=UploadResponse)
def upload(session_id: str = Form(...), file: UploadFile = ...) -> UploadResponse:
    """Validate and store a brief. Sub-second: no model is called here."""
    if not store.is_safe_id(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")

    # Checked BEFORE the read, not after. `store.save_upload` enforces the same
    # ceiling, but it can only do so once `data` is already a bytes object in
    # memory — so the guard sat on the far side of the allocation it exists to
    # prevent. Starlette has already spooled the part to disk past its own
    # threshold and populated `.size`, so this costs nothing and rejects an
    # oversized brief before the 6GB LXC has to hold it.
    max_bytes = docparse_config.upload_max_bytes()
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{file.size} bytes exceeds the {max_bytes}-byte upload limit",
        )

    data = file.file.read()
    # Bound disk on a container that never reboots. No scheduler, no background
    # thread — the clock is read here, at the API layer, and passed down.
    # Proposals ride along on the same trigger: they are smaller and longer-lived
    # than uploads, so they need no trigger of their own, and boot purge is what
    # actually bounds them.
    now = time.time()
    store.sweep(now=now)
    proposal_store.sweep(now=now)

    cached = store.find_by_sha256(session_id, _sha256(data))
    if cached:
        return UploadResponse(
            upload_id=cached.upload_id,
            filename=cached.filename,
            pages=_page_count(cached.path),
            sha256=cached.sha256,
            bytes=cached.path.stat().st_size,
            cached=True,
        )

    upload_id = store.new_upload_id()
    try:
        record = store.save_upload(session_id, upload_id, file.filename or "upload", data)
    except store.UploadTooLarge as ex:
        raise HTTPException(status_code=413, detail=str(ex))

    try:
        pages = _page_count(record.path)
    except (UnsupportedDocument, EncryptedDocument) as ex:
        store.delete_upload(session_id, upload_id)  # never keep what we rejected
        raise HTTPException(status_code=400, detail=str(ex))

    return UploadResponse(
        upload_id=upload_id,
        filename=record.filename,
        pages=pages,
        sha256=record.sha256,
        bytes=len(data),
    )


@app.post("/upload/{upload_id}/parse", response_model=ParseResponse)
def parse_upload(upload_id: str, session_id: str) -> ParseResponse:
    """Transcribe an uploaded brief (hop 1). 4s-30s; own timeout, not /chat's.

    Parsing eagerly at upload moves the wait to the moment the user just
    dropped a file and expects a spinner, rather than into the middle of a chat
    turn where a 90s pause reads as a hang.
    """
    record = store.find_upload(session_id, upload_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no upload {upload_id}")

    # The cache is not an optimization bolted on: the ReAct loop can call the
    # same tool twice in one turn and Streamlit reruns constantly, so without
    # it a redundant request re-runs up to 20 vision calls.
    cached = _cached_parse(record)
    if cached is not None:
        return cached

    t = time.perf_counter()
    llmops.reset_usage()
    try:
        result = transcribe_document(record.path)
    except RuntimeError as ex:  # missing key and other setup problems
        _record_parse(t, session_id, error="RuntimeError")
        raise HTTPException(status_code=503, detail=str(ex))
    except (UnsupportedDocument, EncryptedDocument) as ex:
        _record_parse(t, session_id, error=type(ex).__name__)
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception as ex:
        _record_parse(t, session_id, error=type(ex).__name__)
        raise HTTPException(
            status_code=502, detail=f"transcription failure: {type(ex).__name__}"
        )

    provenance = _provenance(result)
    path = store.save_transcription(
        session_id, upload_id, result.markdown, provenance=provenance
    )
    _record_parse(t, session_id)
    return ParseResponse(upload_id=upload_id, markdown_path=str(path), **provenance)


@app.get("/upload/{upload_id}/transcription")
def get_transcription(upload_id: str, session_id: str) -> FileResponse:
    """Serve hop 1's Markdown artifact over HTTP.

    The UI used to render this by stat-ing ``markdown_path`` on its own
    filesystem — but that path names a file on the *API* host. Running Streamlit
    against the LXC, which ``STRATPOINT_API_URL`` explicitly supports, made
    ``os.path.isfile`` always false and the "View transcription" panel silently
    vanish. Same rule proposals already follow: the UI fetches, it never reads
    a path the API handed it.

    Session-scoped through ``find_upload``, so an id from another session is a
    404 like any other unknown id.
    """
    record = store.find_upload(session_id, upload_id)
    if record is None or not record.transcription_path.is_file():
        raise HTTPException(status_code=404, detail=f"no transcription for {upload_id}")
    return FileResponse(
        record.transcription_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{upload_id}-transcription.md",
        content_disposition_type="inline",
    )


@app.delete("/upload/{upload_id}")
def delete_upload(upload_id: str, session_id: str) -> dict:
    return {"deleted": store.delete_upload(session_id, upload_id)}


# ── proposals ───────────────────────────────────────────────────────────────


def _serve_proposal(session_id: str, proposal_id: str, suffix: str) -> FileResponse:
    """Serve one generated proposal file, session-scoped.

    404 for an unsafe id as much as for a missing file: the two are the same
    fact from the caller's side ("you cannot have this"), and distinguishing
    them tells a prober which ids are shaped correctly.
    """
    path = proposal_store.find_proposal(session_id, proposal_id, suffix)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no proposal {proposal_id}")

    media = "application/pdf" if suffix == ".pdf" else "text/html"
    return FileResponse(
        path,
        media_type=media,
        # inline, not attachment: the UI embeds the HTML twin in a preview pane
        # and offers the PDF through its own download button, which supplies the
        # filename itself.
        filename=f"stratpoint_proposal_{proposal_id}{suffix}",
        content_disposition_type="inline",
    )


@app.get("/proposals/{session_id}/{proposal_id}.pdf")
def download_proposal(session_id: str, proposal_id: str) -> FileResponse:
    """The generated quote. The path in ``PDFGenerationResult.download_url``."""
    return _serve_proposal(session_id, proposal_id, ".pdf")


@app.get("/proposals/{session_id}/{proposal_id}.html")
def preview_proposal(session_id: str, proposal_id: str) -> FileResponse:
    """The HTML the PDF was printed from, for the UI's inline preview.

    Chrome blocks a PDF served from a ``data:`` URI inside Streamlit's sandboxed
    iframe, so previewing the PDF itself is not available; this is the same
    document one render stage earlier.
    """
    return _serve_proposal(session_id, proposal_id, ".html")


@app.delete("/proposals/{session_id}")
def delete_proposals(session_id: str) -> dict:
    """Drop a session's proposals — wired to the UI's 'Reset conversation'."""
    return {"deleted": proposal_store.delete_session(session_id)}


@app.get("/metrics")
def metrics() -> dict:
    """LLMOps view: aggregate metrics + most-recent records (newest first)."""
    recs = llmops.read_records()
    return {"aggregates": llmops.aggregate(recs), "recent": recs[-50:][::-1]}


def _run_eval_layers(judge: bool):
    """Run the registered eval layers, optionally without the live judge.

    Selected by identity, not by name: every layer callable is imported into
    REGISTRY as `layer`, so `fn.__name__` is the string "layer" for five of the
    six and filtering by it would silently drop the wrong one.
    """
    from stratpoint_rag.evaluation import harness
    from stratpoint_rag.evaluation.judge_eval import layer as judge_layer

    layers = harness.REGISTRY if judge else [f for f in harness.REGISTRY if f is not judge_layer]
    return [fn() for fn in layers]


@app.get("/evals")
def evals(judge: bool = True) -> dict:
    """The eval table, for the UI panel (Component #13).

    Served from the API rather than computed in the UI process because the ui
    container mounts none of the volumes the evals read — no trace store, no
    proposals, no corpus — so running the suite there would report empty
    trajectory, e2e and judge layers on every deployment that matters.

    `judge` defaults to True: the LLM-as-a-judge layer is the one the spec names
    explicitly, and it degrades gracefully (failed calls are counted in `detail`
    and the surviving calls still score). It costs ~18s and live LLM calls, so
    it can be turned off for a fast run.
    """
    from stratpoint_rag.evaluation import harness

    results = _run_eval_layers(judge)
    rows = []
    for r in results:
        rows.append({
            "layer": r.layer,
            "name": r.name,
            "passed": r.passed,
            "total": r.total,
            "rate": r.pass_rate,
            "floor": harness.FLOORS.get(r.name),
            "status": "SKIP" if r.skipped else ("FAIL" if harness.below_floor(r) else "ok"),
            "detail": r.detail,
        })
    if not judge:
        # Present but plainly not run, never omitted: a missing row reads as
        # "this project has no judge layer", which is the opposite of true.
        rows.append({
            "layer": "judge", "name": "judge/proposal-quality",
            "passed": 0, "total": 0, "rate": 0.0,
            "floor": harness.FLOORS.get("judge/proposal-quality"),
            "status": "SKIP", "detail": 'not run — tick "include LLM judge"',
        })
    return {
        "rows": rows,
        "ok": not any(harness.below_floor(r) for r in results),
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── helpers ─────────────────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _page_count(path) -> int:
    """Open just far enough to count pages, rejecting encrypted files here
    rather than letting them fail confusingly deep in the page loop."""
    with render.open_document(path) as doc:
        return doc.page_count


def _provenance(result) -> dict:
    return {
        "pages_total": result.pages_total,
        "pages_parsed": result.pages_parsed,
        "pages_failed": result.pages_failed,
        "pages_via_vision": result.pages_via_vision,
        "truncated": result.truncated,
    }


def _cached_parse(record: store.UploadRecord) -> ParseResponse | None:
    """Serve a previous parse from stored metadata.

    Read from meta.json rather than re-parsing the markdown's YAML frontmatter:
    a hand-rolled parser would silently drift from the emitter, and
    pages_via_vision — which the UI chip shows — is not in the frontmatter.
    """
    if record.provenance is None or not record.transcription_path.is_file():
        return None
    return ParseResponse(
        upload_id=record.upload_id,
        markdown_path=str(record.transcription_path),
        **record.provenance,
    )


def _record(
    path: str,
    t_start: float,
    *,
    model: str,
    session_id: str | None = None,
    result: AgentResult | None = None,
    error: str | None = None,
) -> None:
    """Shared telemetry sandwich.

    ``model`` is a parameter, not ``rag_config.llm_model()``: attributing a
    vision parse to the text model would corrupt per-model token accounting,
    and /upload/parse is the single most expensive endpoint in the system.
    """
    usage = llmops.pop_usage() or {}
    llmops.record(
        path,
        (time.perf_counter() - t_start) * 1000,
        error=error,
        session_id=session_id,
        model=model,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        tool_calls=[s.tool for s in result.trace if s.type == "action" and s.tool] if result else None,
        is_grounded=result.is_grounded if result else None,
        confidence=result.confidence if result else None,
        guardrail_reason=result.guardrail_reason if result else None,
    )


def _record_chat(
    t_start: float,
    req: ChatRequest,
    *,
    result: AgentResult | None = None,
    error: str | None = None,
) -> None:
    _record(
        "/chat",
        t_start,
        model=rag_config.llm_model(),
        session_id=req.session_id,
        result=result,
        error=error,
    )


def _record_parse(t_start: float, session_id: str, *, error: str | None = None) -> None:
    # Page counts stay out of telemetry: the record is request-shaped, /metrics
    # is for latency/tokens/errors, and page detail already lives in the
    # provenance block where the user actually sees it.
    _record(
        "/upload/parse",
        t_start,
        model=docparse_config.vision_model(),
        session_id=session_id,
        error=error,
    )
