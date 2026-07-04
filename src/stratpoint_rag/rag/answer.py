"""ponytail: THROWAWAY scaffolding — not the real answer path.

retrieve() + a minimal cloud LLM call, ONLY so RAG can be eval'd/demoed
standalone before the agent module exists. The ReAct agent REPLACES this as
retrieve()'s caller (plan §3.5, Option C). Do not build on this or grow it into
an agent.

Generation runs on the NVIDIA-hosted NIM (OpenAI-compatible chat/completions);
set NVIDIA_API_KEY in .env.
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
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")
    chunks = retrieve(query, k=k)
    context = "\n\n".join(f"[{c.title}] ({c.url})\n{c.text}" for c in chunks)
    prompt = _PROMPT.format(context=context, q=query)
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": config.llm_model(),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16384,
            "temperature": 1.0,
            "top_p": 0.95,
            "stream": False,
            # ponytail: no enable_thinking — grounded RAG answer wants clean text, not CoT
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
