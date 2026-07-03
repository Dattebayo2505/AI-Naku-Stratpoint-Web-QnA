"""Split page markdown into overlapping chunks (plan §3.1).

ponytail: char-budgeted, not token-exact. ~450-token target ≈ 1600 chars at
~3.6 chars/token; bge-small truncates at 512 tokens as a hard backstop, so a
token-dense chunk that overflows just clips its tail. Upgrade to model-tokenizer
counting only if truncation shows up in eval.
"""

from __future__ import annotations

from .models import Chunk

CHARS_PER_CHUNK = 1600  # ~450 tokens
OVERLAP = 300  # ~80 tokens


def split_text(text: str, size: int = CHARS_PER_CHUNK, overlap: int = OVERLAP) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > size:  # a single oversized paragraph: flush, then hard-split it
            if cur:
                chunks.append(cur)
                cur = ""
            step = max(1, size - overlap)
            for i in range(0, len(p), step):
                chunks.append(p[i : i + size])
            continue
        if not cur:
            cur = p
        elif len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}"
        else:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap else ""
            cur = f"{tail}\n\n{p}" if tail else p
    if cur:
        chunks.append(cur)
    return chunks


def chunk_page(page) -> list[Chunk]:
    """Page -> chunks, each stamped with its source url+title for citation."""
    return [
        Chunk(
            id=f"{page.slug}#{i}",
            slug=page.slug,
            url=page.url,
            title=page.title,
            text=t,
        )
        for i, t in enumerate(split_text(page.body))
    ]
