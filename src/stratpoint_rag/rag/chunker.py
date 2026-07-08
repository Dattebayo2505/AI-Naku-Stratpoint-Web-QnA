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

import re

from .models import Chunk

CHARS_PER_CHUNK = 800  # ~220 tokens — small enough to avoid single-fact dilution
OVERLAP = 150  # ~40 tokens

# Markdown links must never be split across a chunk boundary: a chopped
# `[anchor](https://…` or a chunk that starts mid-URL yields an unextractable
# link, so find_resource silently loses the document. Snap hard-split boundaries
# out of any link span so each link lands wholly inside one chunk.
_LINK_SPAN = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _snap_out_of_link(idx: int, spans: list[tuple[int, int]]) -> int:
    """If idx falls strictly inside a link span, move it to that span's start so
    the whole link is pushed into the next chunk. Boundaries at a span edge are
    already safe and returned unchanged."""
    for start, end in spans:
        if start < idx < end:
            return start
    return idx


def _hard_split(p: str, size: int, overlap: int) -> list[str]:
    """Sliding-window split of one oversized paragraph, never cutting a link."""
    spans = [(m.start(), m.end()) for m in _LINK_SPAN.finditer(p)]
    out: list[str] = []
    n = len(p)
    i = 0
    while i < n:
        end = min(i + size, n)
        if end < n:
            snapped = _snap_out_of_link(end, spans)
            # Only accept the snap if it still makes forward progress; a link
            # longer than `size` starting at i can't be avoided, so keep the
            # full window and let that one chunk carry the oversized link.
            if snapped > i:
                end = snapped
        out.append(p[i:end])
        if end >= n:
            break
        nxt = max(i + 1, end - overlap)
        nxt = _snap_out_of_link(nxt, spans)
        i = nxt if nxt > i else end
    return out


def split_text(text: str, size: int = CHARS_PER_CHUNK, overlap: int = OVERLAP) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > size:  # a single oversized paragraph: flush, then hard-split it
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_hard_split(p, size, overlap))
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
