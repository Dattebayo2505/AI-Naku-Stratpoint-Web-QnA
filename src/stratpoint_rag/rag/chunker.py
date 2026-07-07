"""Split page markdown into overlapping chunks (plan §3.1).

ponytail: char-budgeted, not token-exact. bge-small truncates at 512 tokens as
a hard backstop, so a token-dense chunk that overflows just clips its tail.
Upgrade to model-tokenizer counting only if truncation shows up in eval.

Chunk size is deliberately well below the model cap: at ~1600 chars (near the
512-token ceiling) a single high-value sentence is averaged into ~1500 chars of
surrounding prose, diluting its embedding so much that a near-verbatim query
ranks the chunk below dozens of topically-generic ones. Measured: isolating the
same sentence into a ~800-char chunk moved it from retrieval rank ~16/40+ to #0.
Keep chunks small enough that one fact stays a meaningful share of its chunk.
"""

from __future__ import annotations

from .models import Chunk

CHARS_PER_CHUNK = 800  # ~220 tokens — small enough to avoid single-fact dilution
OVERLAP = 150  # ~40 tokens


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
