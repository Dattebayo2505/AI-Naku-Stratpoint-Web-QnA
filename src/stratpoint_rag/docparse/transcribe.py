"""Hop 1: an uploaded brief -> one complete Markdown transcription.

Shape of the run::

    open+validate -> route each page (text layer vs vision) -> rasterize
                  -> fan out vision calls -> assemble -> accumulate usage

Three rules here are easy to break and expensive to debug:

1. **Rasterization happens on the calling thread, not in a worker.** PyMuPDF
   page objects are not thread-safe, and rendering is milliseconds against a
   ~5s network call — there is nothing to gain by moving it and a data race to
   lose. Only the model calls fan out.
2. **Workers never touch llmops.** They return ``(markdown, usage)``; this
   function sums and records once, on the request thread. See clients.py.
3. **Python owns the page wrapper.** The ``## Page N`` heading and the
   provenance comment are emitted here, never asked of the model, because
   ``pages_failed`` accounting depends on the numbering being exact.
4. **A figure page may cost a second call.** When the transcription reply holds
   nothing the page's embedded text layer already had, the vision call bought
   no information and ``_figure_pass`` asks again with a picture-only prompt.
   It is gated on novelty rather than run unconditionally because on a real RFP
   only one page in ten needed it. See ``prompts.FIGURE_PROMPT``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stratpoint_rag import llmops
from stratpoint_rag.docparse import config, prompts, render
from stratpoint_rag.docparse.clients import VisionClient
from stratpoint_rag.docparse.models import PageResult, TranscriptionResult
from stratpoint_rag.docparse.nim import NimVisionClient

log = logging.getLogger(__name__)

__all__ = ["transcribe_document"]

_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

# Output shorter than this is not a transcription of anything.
_MIN_PAGE_CHARS = 20

# A line the model emitted more than this many times is a degeneration loop, not
# the page. Measured: an RFP cover page — one photo, four lines of text — came
# back with eight distinct lines repeated 40x each, 2,048 tokens, truncated at
# the ceiling mid-loop. config.FREQUENCY_PENALTY is the primary fix; this is the
# deterministic backstop, because a sampling parameter is a nudge and the cost
# of it not landing is ~10 KB of invented repetition presented to hop 2 as the
# client's requirements.
#
# 3, not 1: real pages repeat lines a little and legitimately — a table of
# contents with several "Page 9" entries, a table column of identical values, a
# recurring "$1,000,000" limit. Those sit at 2-3; the failure mode sits at 40.
# Occurrences past the third are dropped and the rest of the page is KEPT: the
# cover page's real transcription was correct, only its tail ran away, and
# failing the whole page would have thrown away good content.
_MAX_LINE_REPEATS = 3

# The model sometimes declines instead of transcribing. Treat that as a failed
# page rather than pasting the refusal into the artifact as if it were content.
_REFUSAL = re.compile(
    r"\b(i'm sorry|i am sorry|i cannot|i can't|i'm unable|i am unable|"
    r"unable to (?:read|process|see|assist)|as an ai)\b",
    re.IGNORECASE,
)


def _is_unusable(text: str) -> str | None:
    """Return a failure reason for unusable model output, else None."""
    stripped = (text or "").strip()
    if not stripped:
        return "empty response"
    if _REFUSAL.search(stripped):
        return "refusal"
    if len(stripped) < _MIN_PAGE_CHARS and stripped != "(blank page)":
        return "response too short to be a transcription"
    return None


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")

# Words that say nothing about whether the model looked at the picture: function
# words, and the scaffolding this pipeline's own prompts inject. Counting them
# would let a reply that merely echoed the page score as informative.
_NOVELTY_STOPWORDS = frozenset(
    """
    figure figures table tables page pages image images picture pictures
    the and for with that this are from not but its his her one two
    their will has have was were been being can could would should may might
    all any each some such than then there these those they them our
    your into over under also more most other only same very when where which
    who whom what how why between during before after above below out off
    on in at to of by as is be it or if no do so up we us an am no yes
    """.split()
)


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)} - _NOVELTY_STOPWORDS


def _novelty(reply: str, text_layer: str) -> float:
    """Share of the reply's content words absent from the page's text layer.

    The question this answers is "did the vision call tell us anything the free
    text extraction did not". Near zero on a page that carries a figure means
    the model transcribed the words it could already read and never looked at
    the picture — see prompts.FIGURE_PROMPT for why that happens.
    """
    words = _content_words(reply)
    if not words:
        return 0.0
    return len(words - _content_words(text_layer)) / len(words)


def _collapse_repetition(text: str) -> tuple[str, int]:
    """Drop runaway repeated lines. Returns ``(text, lines_dropped)``.

    Order is preserved and the first ``_MAX_LINE_REPEATS`` occurrences of every
    line survive, so a page that merely repeats a value stays byte-identical and
    only a genuine loop is trimmed. Blank lines are never counted — they are
    Markdown's paragraph separator, not content.
    """
    counts: dict[str, int] = {}
    kept: list[str] = []
    dropped = 0
    for line in text.splitlines():
        key = line.strip()
        if not key:
            kept.append(line)
            continue
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > _MAX_LINE_REPEATS:
            dropped += 1
            continue
        kept.append(line)
    if not dropped:
        return text, 0
    # A trimmed loop leaves a run of blank lines behind it; collapse them so the
    # artifact does not end in a page of whitespace.
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out, dropped


def _needs_vision(doc: render.Document, index: int, min_chars: int) -> bool:
    """Decide the route for one page.

    Most real briefs are digitally generated and carry a perfect embedded text
    layer. Running those through an 11B vision model pays latency and tokens to
    lossily re-derive text we already have *exactly* — a 30-page digital RFP
    would cost 30 vision calls (~150s, past the UI's client timeout) instead of
    zero. Accuracy runs the same direction: the text layer is ground truth, the
    vision model is a guess at it.
    """
    if doc.kind == "image":
        return True  # no text layer exists to check
    if len(doc.page_text(index).strip()) < min_chars:
        return True
    # A page can carry both real text and a diagram; the diagram holds
    # requirements the text layer misses, so it still earns a vision call.
    return doc.page_has_large_image(index)


def _figure_pass(
    page_no: int, image: bytes, vision: VisionClient
) -> tuple[str, dict]:
    """Second, figure-only call. Returns ``(block, usage)``; block may be ''.

    Soft-fails to '' on any error: this runs only on a page whose first pass
    already produced usable text, so losing the figure block is a degradation,
    never a reason to fail the page.
    """
    try:
        reply, usage = vision.describe(
            image, prompts.FIGURE_PROMPT, prompts.FIGURE_USER_TURN
        )
    except Exception as e:
        log.warning("page %d figure pass failed: %s", page_no, e)
        return "", {}

    text = (reply or "").strip()
    low = text.lower()
    if not text or any(m in low for m in prompts.NO_FIGURES_MARKERS):
        return "", usage  # declined: the page has no picture. Usage still counts.
    if _is_unusable(text):
        return "", usage
    block, _ = _collapse_repetition(text)
    return block, usage


def _render_page(
    page_no: int,
    image: bytes,
    vision: VisionClient,
    text_layer: str = "",
) -> PageResult:
    """Runs in a worker thread. Returns usage; never accumulates it."""
    try:
        markdown, usage = vision.describe(image, prompts.TRANSCRIPTION_PROMPT)
    except Exception as e:  # soft-fail one page, the crawler's precedent
        log.warning("page %d vision call failed: %s", page_no, e)
        return PageResult(page_no, "", "vision", failed=True, failure_reason=str(e))

    reason = _is_unusable(markdown)
    if reason:
        return PageResult(page_no, "", "vision", failed=True, failure_reason=reason)

    markdown, dropped = _collapse_repetition(markdown.strip())
    note = None
    if dropped:
        note = f"collapsed {dropped} repeated lines"
        log.warning("page %d: %s (model degeneration loop)", page_no, note)
    # Collapsing can strip a reply that was *entirely* loop; re-check.
    reason = _is_unusable(markdown)
    if reason:
        return PageResult(page_no, "", "vision", failed=True, failure_reason=reason)

    # This page was routed to vision because it carries a figure. If the reply
    # holds nothing the text layer did not already have, the call bought no
    # information — ask again, with a prompt whose only job is the picture.
    if (
        len(text_layer.strip()) >= config.figure_pass_min_text_chars()
        and _novelty(markdown, text_layer) < config.figure_pass_novelty()
    ):
        block, fig_usage = _figure_pass(page_no, image, vision)
        for k in _USAGE_KEYS:
            usage[k] = (usage.get(k) or 0) + (fig_usage.get(k) or 0)
        if block:
            markdown = f"{markdown}\n\n{block}"
            note = "; ".join(filter(None, [note, "figure pass"]))
            log.info("page %d: figure pass added %d chars", page_no, len(block))

    return PageResult(page_no, markdown, "vision", usage=usage, note=note)


def _frontmatter(result_fields: dict) -> str:
    failed = ", ".join(str(n) for n in result_fields["pages_failed"])
    lines = [
        "---",
        f"source_file: {result_fields['source_file']}",
        f"sha256: {result_fields['sha256']}",
        f"pages_total: {result_fields['pages_total']}",
        f"pages_parsed: {result_fields['pages_parsed']}",
        f"pages_failed: [{failed}]",
    ]
    if result_fields["truncated"]:
        lines.append(
            f"truncated: true  # only the first {result_fields['pages_parsed']} "
            "pages were parsed"
        )
    lines.append("---")
    return "\n".join(lines)


def _assemble(pages: list[PageResult], header: str) -> str:
    """Wrap each page's body in the Python-owned heading + provenance comment."""
    blocks = [header]
    for page in pages:
        # ASCII only: this artifact is read back on Windows consoles and fed to
        # downstream tooling, and a stray non-ASCII separator buys nothing.
        marker = f"<!-- page {page.number} | source: {page.source}"
        if page.failed:
            marker += f" | FAILED: {page.failure_reason}"
        if page.note:
            marker += f" | {page.note}"
        marker += " -->"
        blocks.append(f"## Page {page.number}\n{marker}")
        if page.markdown:
            blocks.append(page.markdown)
    return "\n\n".join(blocks) + "\n"


def transcribe_document(
    path: str | Path, *, vision: VisionClient | None = None
) -> TranscriptionResult:
    """Transcribe an uploaded brief into one Markdown document.

    Per-page failures are soft: the page is recorded in ``pages_failed``, a
    marker is stamped in place, and the run continues — a partial transcription
    beats a hard abort. Only setup problems (an unopenable or encrypted file)
    raise.

    ``vision`` is injected for tests; production passes a ``NimVisionClient``.
    """
    path = Path(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    min_chars = config.text_layer_min_chars()

    with render.open_document(path) as doc:
        pages_total = doc.page_count
        limit = min(pages_total, config.max_pages())

        # Phase 1 — route, and rasterize what needs the model. Single-threaded:
        # PyMuPDF pages are not thread-safe and rendering is milliseconds.
        done: list[PageResult] = []
        # The text layer rides along so a worker can tell whether its reply added
        # anything to it; the document is closed before the pool starts.
        pending: list[tuple[int, bytes, str]] = []
        for i in range(limit):
            page_no = i + 1
            if _needs_vision(doc, i, min_chars):
                pending.append((page_no, doc.rasterize(i), doc.page_text(i)))
            else:
                done.append(PageResult(page_no, doc.page_text(i).strip(), "text"))

    # Phase 2 — fan out the model calls only. One image per request: the
    # endpoint hard-refuses two with HTTP 400 before inference. The client is
    # built lazily, so a fully digital brief parses with no key configured.
    if pending:
        vision = vision or NimVisionClient()
        workers = max(1, min(config.concurrency(), len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done.extend(
                pool.map(lambda a: _render_page(a[0], a[1], vision, a[2]), pending)
            )

    pages = sorted(done, key=lambda p: p.number)

    # Phase 3 — account for it all, on this thread.
    usage = {k: 0 for k in _USAGE_KEYS}
    for page in pages:
        for k in _USAGE_KEYS:
            usage[k] += (page.usage or {}).get(k, 0) or 0
    if usage["total_tokens"] or usage["prompt_tokens"]:
        llmops.add_usage(usage)

    failed = [p.number for p in pages if p.failed]
    fields = {
        "source_file": path.name,
        "sha256": sha256,
        "pages_total": pages_total,
        "pages_parsed": len(pages) - len(failed),
        "pages_failed": failed,
        "truncated": limit < pages_total,
    }
    return TranscriptionResult(
        markdown=_assemble(pages, _frontmatter(fields)),
        pages_via_vision=sum(1 for p in pages if p.source == "vision"),
        usage=usage,
        **fields,
    )
