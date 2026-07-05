# ReAct Agent + API Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ReAct customer-support agent for stratpoint.com (two grounded tools) and a FastAPI `/chat` endpoint exposing it.

**Architecture:** A LangChain `create_react_agent` (LangGraph) runs a reason/act loop over the NVIDIA NIM cloud endpoint via `ChatNVIDIA`. It has two tools — `search_stratpoint` (wraps the existing `rag.answer()` grounded path) and `find_resource` (extracts downloadable PDF links from retrieved corpus chunks). A thin FastAPI adapter validates requests, calls the agent, and serializes the result (answer + citations + resources + reasoning trace).

**Tech Stack:** Python 3.13, uv; LangChain (`langchain-nvidia-ai-endpoints`, `langgraph`, `langchain-core`); FastAPI + uvicorn; pytest.

## Global Constraints

- Python `>=3.13`, uv-managed — use `uv add <pkg>` and `uv run <cmd>`; never edit `pyproject.toml` deps by hand.
- **Reuse RAG seams, do not reimplement them:**
  - `answer(query: str, k: int = 5) -> str` from `stratpoint_rag.rag.answer`
  - `retrieve(query: str, k: int = 5) -> list[Chunk]` from `stratpoint_rag.rag.retrieve`
  - config getters `nvidia_api_key()`, `nvidia_base_url()`, `llm_model()` from `stratpoint_rag.rag.config`
  - `Chunk` (frozen dataclass) fields: `id, slug, url, title, text, score` from `stratpoint_rag.rag.models`
- **Agent style is fixed:** LangChain `ChatNVIDIA` + `create_react_agent` from **`langgraph.prebuilt`**. **NEVER** use `langchain.agents.create_agent` — NVIDIA docs warn it "bypasses the tool execution loop".
- **Do NOT** set `response_format={"type":"json_object"}` anywhere in the agent. Grounded-JSON output stays inside `rag.answer()`'s own call; the agent does tool routing only.
- `stratpoint_rag` must not import from `stratpoint_crawl`.
- Model/endpoint already verified (2026-07-05): `google/gemma-4-31b-it` @ `https://integrate.api.nvidia.com/v1` returns proper OpenAI-style tool calls.
- Tests are **offline by default**. Any live-network test is marked `@pytest.mark.integration` (the repo's `addopts = "-m 'not integration'"` deselects it). New tests go in `tests/`.
- Lead capture (`request_callback`) is **out of scope** for this plan (deferred increment).

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml` (via `uv add` — do not hand-edit)
- Test: `tests/test_agent_deps.py`

**Interfaces:**
- Consumes: nothing
- Produces: importable packages `langchain_nvidia_ai_endpoints`, `langgraph`, `langchain_core`, `fastapi`, `uvicorn`.

- [ ] **Step 1: Add the runtime dependencies**

Run:
```bash
uv add langchain-nvidia-ai-endpoints langgraph langchain-core fastapi uvicorn
```
Expected: `uv` resolves and writes them into `pyproject.toml` `[project.dependencies]` and updates `uv.lock`.

- [ ] **Step 2: Write a smoke-import test**

```python
# tests/test_agent_deps.py
def test_agent_dependencies_importable():
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    import langchain_core  # noqa: F401
    import langgraph  # noqa: F401
    from langchain_nvidia_ai_endpoints import ChatNVIDIA  # noqa: F401
    from langgraph.prebuilt import create_react_agent  # noqa: F401
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `uv run pytest tests/test_agent_deps.py -v`
Expected: PASS (1 passed). If `ImportError`, the dependency add failed — re-run Step 1.

- [ ] **Step 4: Commit** (the repo owner runs commits — see handoff note)

```bash
git add pyproject.toml uv.lock tests/test_agent_deps.py
git commit -m "build: add langchain, langgraph, fastapi deps for agent + api"
```

---

### Task 2: Agent tools module

**Files:**
- Create: `src/stratpoint_rag/agent/tools.py`
- Test: `tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `rag.answer`, `rag.retrieve`, `Chunk` (see Global Constraints)
- Produces:
  - `_extract_doc_links(text: str) -> list[tuple[str, str]]` — `(title, url)` pairs
  - `search_stratpoint` and `find_resource` — LangChain tools (call via `.invoke(...)`)
  - `TOOLS: list` — `[search_stratpoint, find_resource]`
  - Both tools emit downloadable/citation entries as `- {title} ({url})` lines (a format Task 3's parser relies on).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_tools.py
from stratpoint_rag.agent import tools
from stratpoint_rag.rag.models import Chunk


def test_extract_doc_links_finds_pdf_and_strips_markdown():
    text = "see [**35% cost reduction**](https://pages.awscloud.com/x/the-value.pdf) now"
    assert tools._extract_doc_links(text) == [
        ("35% cost reduction", "https://pages.awscloud.com/x/the-value.pdf")
    ]


def test_extract_doc_links_dedupes_and_ignores_non_docs():
    text = (
        "[a](https://s.com/f.pdf) [b](https://s.com/f.pdf) "
        "[c](https://s.com/page) [d](https://s.com/deck.pptx)"
    )
    assert tools._extract_doc_links(text) == [
        ("a", "https://s.com/f.pdf"),
        ("d", "https://s.com/deck.pptx"),
    ]


def test_find_resource_lists_links_from_retrieved_chunks(monkeypatch):
    chunk = Chunk(
        id="1", slug="s", url="https://stratpoint.com/p", title="P",
        text="ref [AWS whitepaper](https://aws.com/wp.pdf)", score=0.9,
    )
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    out = tools.find_resource.invoke("cloud")
    assert "- AWS whitepaper (https://aws.com/wp.pdf)" in out


def test_find_resource_reports_when_none(monkeypatch):
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="no links here", score=0.1)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    assert "No downloadable resources" in tools.find_resource.invoke("cloud")


def test_search_stratpoint_delegates_to_rag_answer(monkeypatch):
    monkeypatch.setattr(tools, "_rag_answer", lambda q: "grounded answer for " + q)
    assert tools.search_stratpoint.invoke("services") == "grounded answer for services"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_tools.py -v`
Expected: FAIL (collection error / `ModuleNotFoundError: stratpoint_rag.agent.tools`).

- [ ] **Step 3: Write the implementation**

```python
# src/stratpoint_rag/agent/tools.py
"""Agent tools: corpus-grounded Q&A and downloadable-resource lookup.

Both are LangChain @tool functions; the docstrings drive tool selection, so
keep them descriptive. These are the only tools the ReAct agent may call.
"""
from __future__ import annotations

import re

from langchain_core.tools import tool

from stratpoint_rag.rag.answer import answer as _rag_answer
from stratpoint_rag.rag.retrieve import retrieve as _retrieve

# Markdown links whose target is a downloadable document.
_DOC_LINK = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+?\.(?:pdf|docx?|pptx?)(?:\?[^\s)]*)?)\)",
    re.IGNORECASE,
)


def _extract_doc_links(text: str) -> list[tuple[str, str]]:
    """Return [(title, url)] for downloadable-doc links in markdown text (deduped)."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, url in _DOC_LINK.findall(text or ""):
        if url in seen:
            continue
        seen.add(url)
        title = label.strip(" *_") or url
        out.append((title, url))
    return out


@tool
def search_stratpoint(query: str) -> str:
    """Answer a question about Stratpoint using the company's website content.
    Use for anything about Stratpoint's services, company, case studies, or blog.

    Args:
        query: The visitor's question, e.g. 'Do you offer cloud migration?'
    """
    try:
        return _rag_answer(query)
    except Exception as ex:  # surfaced as an Observation so the loop can recover
        return f"search_stratpoint error: {type(ex).__name__}: {ex}"


@tool
def find_resource(topic: str) -> str:
    """Find downloadable resources (PDFs/whitepapers) related to a topic, drawn
    from Stratpoint's website content. Use when the visitor wants something to
    read or download.

    Args:
        topic: The subject to find resources for, e.g. 'supply chain cloud'
    """
    try:
        chunks = _retrieve(topic, k=5)
    except Exception as ex:
        return f"find_resource error: {type(ex).__name__}: {ex}"

    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for c in chunks:
        for title, url in _extract_doc_links(c.text):
            if url in seen:
                continue
            seen.add(url)
            links.append((title, url))

    if not links:
        return f"No downloadable resources found for '{topic}'."
    lines = "\n".join(f"- {t} ({u})" for t, u in links)
    return f"Downloadable resources for '{topic}':\n{lines}"


TOOLS = [search_stratpoint, find_resource]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_tools.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_rag/agent/tools.py tests/test_agent_tools.py
git commit -m "feat(agent): add search_stratpoint and find_resource tools"
```

---

### Task 3: Result models + trace extraction

**Files:**
- Create: `src/stratpoint_rag/agent/agent.py` (models + pure extraction only; the runner is added in Task 4)
- Test: `tests/test_agent_result.py`

**Interfaces:**
- Consumes: nothing (pure functions over LangChain message objects)
- Produces:
  - `Link(BaseModel)`: `title: str, url: str`
  - `Step(BaseModel)`: `type: str, tool: str | None, tool_input: dict | None, content: str | None`
  - `AgentResult(BaseModel)`: `answer: str, citations: list[Link], resources: list[Link], trace: list[Step]`
  - `_parse_link_lines(text: str) -> list[Link]`
  - `_build_result(messages: list) -> AgentResult`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_result.py
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from stratpoint_rag.agent import agent


def test_parse_link_lines():
    text = "Sources used:\n- Cloud page (https://stratpoint.com/cloud)\n- X (https://x.com/f.pdf)"
    links = agent._parse_link_lines(text)
    assert [(l.title, l.url) for l in links] == [
        ("Cloud page", "https://stratpoint.com/cloud"),
        ("X", "https://x.com/f.pdf"),
    ]


def test_build_result_captures_answer_trace_citations_resources():
    messages = [
        HumanMessage(content="cloud migration + a whitepaper?"),
        AIMessage(content="", tool_calls=[
            {"name": "search_stratpoint", "args": {"query": "cloud migration"}, "id": "c1"}
        ]),
        ToolMessage(
            content="We offer cloud migration.\n\nSources used:\n- Cloud (https://stratpoint.com/cloud)",
            name="search_stratpoint", tool_call_id="c1",
        ),
        AIMessage(content="", tool_calls=[
            {"name": "find_resource", "args": {"topic": "cloud"}, "id": "c2"}
        ]),
        ToolMessage(
            content="Downloadable resources for 'cloud':\n- AWS WP (https://aws.com/wp.pdf)",
            name="find_resource", tool_call_id="c2",
        ),
        AIMessage(content="Yes — we do cloud migration; here's a whitepaper."),
    ]
    result = agent._build_result(messages)
    assert result.answer == "Yes — we do cloud migration; here's a whitepaper."
    assert [c.url for c in result.citations] == ["https://stratpoint.com/cloud"]
    assert [r.url for r in result.resources] == ["https://aws.com/wp.pdf"]
    assert [s.type for s in result.trace] == [
        "action", "observation", "action", "observation", "answer",
    ]


def test_build_result_tool_free_answer():
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="Hello! I'm Stratpoint's assistant."),
    ]
    result = agent._build_result(messages)
    assert result.answer == "Hello! I'm Stratpoint's assistant."
    assert result.trace == [agent.Step(type="answer", content=result.answer)]
    assert result.citations == [] and result.resources == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_result.py -v`
Expected: FAIL (`ModuleNotFoundError: stratpoint_rag.agent.agent`).

- [ ] **Step 3: Write the implementation**

```python
# src/stratpoint_rag/agent/agent.py
"""ReAct agent over the NVIDIA NIM cloud endpoint.

This module holds the result models + trace extraction (pure) and, added in a
later task, the agent runner. Public seam: run_agent(message, history=None).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


class Link(BaseModel):
    title: str
    url: str


class Step(BaseModel):
    type: str  # "thought" | "action" | "observation" | "answer"
    tool: str | None = None
    tool_input: dict | None = None
    content: str | None = None


class AgentResult(BaseModel):
    answer: str
    citations: list[Link] = []
    resources: list[Link] = []
    trace: list[Step] = []


_LINK_LINE = re.compile(r"^- (.+?) \((https?://[^)]+)\)\s*$", re.MULTILINE)


def _parse_link_lines(text: str) -> list[Link]:
    """Parse '- title (url)' lines into Links (used for both citations & resources)."""
    return [Link(title=t.strip(), url=u.strip()) for t, u in _LINK_LINE.findall(text or "")]


def _build_result(messages: list[Any]) -> AgentResult:
    """Fold a LangGraph message list into an AgentResult (answer/trace/citations/resources)."""
    trace: list[Step] = []
    citations: list[Link] = []
    resources: list[Link] = []
    answer = ""

    for m in messages:
        mtype = getattr(m, "type", None)
        tool_calls = getattr(m, "tool_calls", None) or []
        content = getattr(m, "content", "") or ""

        if mtype == "ai":
            if content and tool_calls:
                trace.append(Step(type="thought", content=content))
            elif content:
                answer = content
                trace.append(Step(type="answer", content=content))
            for tc in tool_calls:
                trace.append(Step(type="action", tool=tc["name"], tool_input=tc.get("args") or {}))
        elif mtype == "tool":
            name = getattr(m, "name", None)
            trace.append(Step(type="observation", tool=name, content=content))
            if name == "search_stratpoint":
                citations.extend(_parse_link_lines(content))
            elif name == "find_resource":
                resources.extend(_parse_link_lines(content))

    return AgentResult(answer=answer, citations=citations, resources=resources, trace=trace)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_result.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_rag/agent/agent.py tests/test_agent_result.py
git commit -m "feat(agent): add result models and trace extraction"
```

---

### Task 4: Agent runner + public seam

**Files:**
- Modify: `src/stratpoint_rag/agent/agent.py` (append runner below the models from Task 3)
- Modify: `src/stratpoint_rag/agent/__init__.py`
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `TOOLS` (Task 2); `_build_result`, `AgentResult` (Task 3); `config` getters
- Produces:
  - `SYSTEM_PROMPT: str`
  - `run_agent(message: str, history: list[dict] | None = None, *, agent=None) -> AgentResult`
  - `_build_agent()` / `_get_agent()` (lazy singleton; injected `agent=` bypasses them in tests)
  - `stratpoint_rag.agent` package re-exports `run_agent`, `AgentResult`, `Link`, `Step`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent_runner.py
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from stratpoint_rag.agent import agent


class _FakeAgent:
    """Stands in for a compiled create_react_agent; records the input, returns canned messages."""
    def __init__(self, messages):
        self._messages = messages
        self.seen = None

    def invoke(self, payload, config=None):
        self.seen = payload
        return {"messages": self._messages}


def test_run_agent_returns_agentresult_from_injected_agent():
    fake = _FakeAgent([
        HumanMessage(content="services?"),
        AIMessage(content="We build software, cloud, data, and AI solutions."),
    ])
    result = agent.run_agent("What services do you offer?", agent=fake)
    assert result.answer == "We build software, cloud, data, and AI solutions."
    # the user message is threaded into the agent payload
    assert fake.seen["messages"][-1] == ("user", "What services do you offer?")


def test_run_agent_threads_history():
    fake = _FakeAgent([AIMessage(content="ok")])
    agent.run_agent(
        "and pricing?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        agent=fake,
    )
    assert fake.seen["messages"] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "and pricing?"),
    ]


def test_agent_package_reexports():
    import stratpoint_rag.agent as pkg
    assert hasattr(pkg, "run_agent") and hasattr(pkg, "AgentResult")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'run_agent'`).

- [ ] **Step 3: Append the runner to `agent.py`**

Add to the **end** of `src/stratpoint_rag/agent/agent.py`:

```python

from stratpoint_rag.rag import config
from stratpoint_rag.agent.tools import TOOLS

SYSTEM_PROMPT = (
    "You are Stratpoint's website customer-support assistant.\n"
    "Use the search_stratpoint tool for any question about Stratpoint's services, "
    "company, case studies, or blog. Use the find_resource tool when the visitor "
    "wants something to read or download.\n"
    "For greetings or questions about yourself, answer directly without a tool.\n"
    "Base factual claims on tool results, not memory. If a question is missing a "
    "detail you need, ask for it in plain language. Be concise and helpful."
)

_agent = None


def _build_agent():
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    from langgraph.prebuilt import create_react_agent

    llm = ChatNVIDIA(
        base_url=config.nvidia_base_url(),
        model=config.llm_model(),
        api_key=key,
        temperature=0.2,
    )
    # NOTE: prompt= is the current langgraph API. If the installed version rejects
    # it, use state_modifier=SYSTEM_PROMPT instead.
    return create_react_agent(llm, TOOLS, prompt=SYSTEM_PROMPT)


def _get_agent():
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


def run_agent(message: str, history: list[dict] | None = None, *, agent=None) -> AgentResult:
    """Run one turn of the ReAct agent and return a structured AgentResult.

    `agent` is an injection seam for tests; production uses the lazy singleton.
    """
    if agent is None:
        agent = _get_agent()
    msgs: list = [(h["role"], h["content"]) for h in (history or [])]
    msgs.append(("user", message))
    state = agent.invoke({"messages": msgs}, config={"recursion_limit": 8})
    return _build_result(state["messages"])
```

- [ ] **Step 4: Update the package exports**

Replace the contents of `src/stratpoint_rag/agent/__init__.py` with:

```python
"""ReAct agent: reason/act loop orchestrating retrieval + resource tools."""
from stratpoint_rag.agent.agent import AgentResult, Link, Step, run_agent

__all__ = ["run_agent", "AgentResult", "Link", "Step"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full agent test set**

Run: `uv run pytest tests/test_agent_tools.py tests/test_agent_result.py tests/test_agent_runner.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_rag/agent/agent.py src/stratpoint_rag/agent/__init__.py tests/test_agent_runner.py
git commit -m "feat(agent): add ReAct runner over ChatNVIDIA + create_react_agent"
```

---

### Task 5: FastAPI `/chat` endpoint

**Files:**
- Create: `src/stratpoint_rag/api/app.py`
- Modify: `src/stratpoint_rag/api/__init__.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `run_agent`, `AgentResult` from `stratpoint_rag.agent`
- Produces:
  - FastAPI `app` with `POST /chat` (body `{message, history?, session_id?}` → `AgentResult`) and `GET /health`
  - `stratpoint_rag.api` re-exports `app`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py
import pytest
from fastapi.testclient import TestClient

from stratpoint_rag.agent import AgentResult, Link, Step
from stratpoint_rag.api import app as app_module

client = TestClient(app_module.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_chat_returns_serialized_agent_result(monkeypatch):
    canned = AgentResult(
        answer="We do cloud migration.",
        citations=[Link(title="Cloud", url="https://stratpoint.com/cloud")],
        resources=[],
        trace=[Step(type="answer", content="We do cloud migration.")],
    )
    monkeypatch.setattr(app_module, "run_agent", lambda message, history=None: canned)
    r = client.post("/chat", json={"message": "do you do cloud migration?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "We do cloud migration."
    assert body["citations"][0]["url"] == "https://stratpoint.com/cloud"


def test_chat_rejects_missing_message():
    r = client.post("/chat", json={})
    assert r.status_code == 422


def test_chat_maps_runtime_error_to_503(monkeypatch):
    def boom(message, history=None):
        raise RuntimeError("NVIDIA_API_KEY is not set")
    monkeypatch.setattr(app_module, "run_agent", boom)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 503


def test_chat_maps_upstream_error_to_502(monkeypatch):
    def boom(message, history=None):
        raise ValueError("endpoint exploded")
    monkeypatch.setattr(app_module, "run_agent", boom)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL (`ModuleNotFoundError: stratpoint_rag.api.app`).

- [ ] **Step 3: Write the app**

```python
# src/stratpoint_rag/api/app.py
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
```

- [ ] **Step 4: Update the package exports**

Replace the contents of `src/stratpoint_rag/api/__init__.py` with:

```python
"""HTTP API exposing the chatbot (FastAPI)."""
from stratpoint_rag.api.app import app

__all__ = ["app"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add src/stratpoint_rag/api/app.py src/stratpoint_rag/api/__init__.py tests/test_api.py
git commit -m "feat(api): add FastAPI /chat and /health endpoints"
```

---

### Task 6: Live integration test + run docs

**Files:**
- Create: `tests/test_agent_integration.py`
- Modify: `README.md` (add an "Agent + API" usage snippet)

**Interfaces:**
- Consumes: `stratpoint_rag.api.app.app`; requires a built `chroma_db/` and a real `NVIDIA_API_KEY`
- Produces: an opt-in end-to-end check; user-facing run instructions

- [ ] **Step 1: Write the integration test (opt-in, deselected by default)**

```python
# tests/test_agent_integration.py
import pytest
from fastapi.testclient import TestClient

from stratpoint_rag.api.app import app


@pytest.mark.integration
def test_chat_live_end_to_end():
    """Hits the real NIM endpoint + local Chroma store. Requires:
    - `uv run stratpoint-rag-ingest` has built chroma_db/
    - NVIDIA_API_KEY set in .env
    Run with: uv run pytest -m integration tests/test_agent_integration.py -v
    """
    client = TestClient(app)
    r = client.post("/chat", json={"message": "What services does Stratpoint offer?"})
    assert r.status_code == 200
    assert r.json()["answer"].strip()
```

- [ ] **Step 2: Verify it is deselected by the default run**

Run: `uv run pytest tests/test_agent_integration.py -v`
Expected: `1 deselected` (the default `addopts = "-m 'not integration'"` skips it) — no network hit.

- [ ] **Step 3: Run the whole offline suite to confirm nothing regressed**

Run: `uv run pytest -v`
Expected: PASS for all agent/api tests; the one integration test deselected; existing crawler tests unaffected.

- [ ] **Step 4: (Optional, manual) run the live check**

Only if `chroma_db/` is built and `NVIDIA_API_KEY` is set:
Run: `uv run pytest -m integration tests/test_agent_integration.py -v`
Expected: PASS (real answer returned).

- [ ] **Step 5: Add a usage snippet to `README.md`**

Under the existing usage section, add:

```markdown
## Usage — Agent + API

Build the retrieval index first (one-time; regenerated from `data/`):

    uv run stratpoint-rag-ingest

Serve the chatbot API (requires `NVIDIA_API_KEY` in `.env`):

    uv run uvicorn stratpoint_rag.api.app:app --port 8000

Then POST a message:

    curl -s http://localhost:8000/chat \
      -H "Content-Type: application/json" \
      -d '{"message": "Do you offer cloud migration?"}'

Response shape: `{ "answer", "citations": [{title,url}], "resources": [{title,url}], "trace": [...] }`.
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_agent_integration.py README.md
git commit -m "test(agent): add opt-in live integration test + API run docs"
```

---

## Notes for the executor

- **Commits:** the repo owner runs all `git commit` steps themselves. When a task reaches its commit step, stage nothing on their behalf — surface the suggested command and pause for them to commit.
- **`prompt=` vs `state_modifier=`:** if `create_react_agent(..., prompt=SYSTEM_PROMPT)` raises a `TypeError` about an unexpected keyword, the installed langgraph is older — switch that one call to `state_modifier=SYSTEM_PROMPT`.
- **Working branch:** `feat/agent-and-api-integration` (already created).
