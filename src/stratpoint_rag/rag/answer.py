"""ponytail: THROWAWAY scaffolding — not the real answer path.

retrieve() + a minimal Ollama call, ONLY so RAG can be eval'd/demoed standalone
before the agent module exists. The ReAct agent REPLACES this as retrieve()'s
caller (plan §3.5, Option C). Do not build on this or grow it into an agent.
"""

from __future__ import annotations

import httpx

from . import config
from .retrieve import retrieve

_PROMPT = """Answer the question using ONLY the Stratpoint context below. \
If the answer isn't in the context, say you don't know. Cite the source URLs you used.

Context:
{context}

Question: {q}
Answer:"""


def answer(query: str, k: int = 5) -> str:
    chunks = retrieve(query, k=k)
    context = "\n\n".join(f"[{c.title}] ({c.url})\n{c.text}" for c in chunks)
    prompt = _PROMPT.format(context=context, q=query)
    resp = httpx.post(
        f"{config.ollama_host()}/api/generate",
        json={"model": config.llm_model(), "prompt": prompt, "stream": False},
        timeout=600,  # ponytail: CPU inference is slow; generous cap so it doesn't time out
    )
    resp.raise_for_status()
    return resp.json()["response"]
