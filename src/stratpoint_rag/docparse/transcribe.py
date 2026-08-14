"""Hop 1: an uploaded brief -> one complete Markdown transcription.

Shape of the run::

    open+validate -> route each page (text layer vs vision) -> rasterize
                  -> fan out vision calls -> assemble -> accumulate usage

Five rules here are easy to break and expensive to debug:

1. **Rasterization happens on the calling thread, not in a worker.** PyMuPDF
   page objects are not thread-safe, and rendering is milliseconds against a
   ~5s network call — there is nothing to gain by moving it and a data race to
   lose. Only the model calls fan out.
2. **Workers never touch llmops.** They return ``(markdown, usage)``; this
   function sums and records once, on the request thread. See clients.py.
3. **Python owns the page wrapper.** The ``## Page N`` heading and the
   provenance comment are emitted here, never asked of the model, because
   ``pages_failed`` accounting depends on the numbering being exact.
4. **A figure page may cost a second call.** When no described figure came back,
   or when the reply holds nothing already known about the page, the vision call
   bought no picture and ``_figure_pass`` asks again with a picture-only prompt.
   The second trigger needs a baseline to measure against and the first does not
   — binding both to a text layer made the pass unreachable on scanned briefs,
   which are the documents that need it most. A per-document budget bounds the
   cost, because on a scan "no figure block" is also true of every text page.
   See ``prompts.FIGURE_PROMPT``.
5. **Shallow headings are clamped, in-page only.** Nemotron emits ``#`` and
   ``##`` headings in about half of runs despite the prompt rule, and a ``##``
   in a page body collides with the wrapper in rule 3. ``_clamp_headings``
   demotes them to ``###``. This never compares two pages — it is not the
   cross-page heading normalization ``prompts.py`` forbids.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stratpoint_rag import llmops
from stratpoint_rag.docparse import config, prompts, render, slides
from stratpoint_rag.docparse.clients import VisionClient
from stratpoint_rag.docparse.models import PageResult, TranscriptionResult
from stratpoint_rag.docparse.nim import NimVisionClient

log = logging.getLogger(__name__)

__all__ = ["transcribe_document"]

_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

# Output shorter than this is not a transcription of anything.
_MIN_PAGE_CHARS = 20

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


# The prompt restricts the model to ### and deeper; nemotron obeys it in about
# half of runs. Measured over six runs of one 10-page scan, three emitted
# shallow headings: "# General Information", "## Deliverables" (x3),
# "## Site Information", "# Major Tree Varieties in new Civic Park" (x3).
#
# A ## in a page body claims the level of the `## Page N` wrapper Python owns,
# and pages_failed accounting depends on that wrapper being unambiguous. This
# follows the precedent frequency_penalty set before it was removed: a prompt
# rule is a nudge, and where the cost of it not landing is a corrupted artifact,
# the backstop is deterministic.
#
# This is a WITHIN-page clamp and is NOT the cross-page heading normalization
# prompts.py forbids. That rule bans inferring one document hierarchy from N
# independent page guesses; this never compares two pages, and never moves a
# heading relative to its neighbours on the same page.
#
# Accepted limitation: a "# comment" line inside a fenced code block would also
# be demoted. Briefs are prose, tables and diagrams; the trade is worth it and
# the alternative is a fence-state parser for a case not yet observed.
_SHALLOW_HEADING = re.compile(r"^#{1,2}(?!#)(?=\s|$)", re.M)


def _clamp_headings(text: str) -> str:
    """Demote level-1 and level-2 headings in a page body to level 3."""
    return _SHALLOW_HEADING.sub("###", text)


# Numbers are content. The second alternative is not decoration: until
# 2026-08-09 this pattern required a leading letter, so a year, an amount or a
# count was never a content word, and `_novelty` could not see the single most
# decisive evidence that a vision call looked at a picture.
#
# Measured on RFP page 5, two aerial maps labelled "Civic Park - 2023", "Tower
# Park - 2025", "Yanaguana Garden - 2015". Those place names are already in the
# page's prose, so with the years invisible a correct reading of the maps scored
# 0.048 novelty and was dropped as an echo — 16 times out of 16 probed replies,
# every one of which had read the map. The page carried no figure block across
# every run of two sessions and looked like a model failure; the model had done
# the work each time and this regex threw it away. With numbers counted the same
# replies score 0.167.
#
# Counting them on BOTH sides of the ratio is what keeps the safety valve: a
# reply re-serving figures the page already prints gains nothing, which is how a
# table misread as a picture stays out of the artifact (measured 0.045 -> 0.042).
# No page of the 10-page corpus changes its trigger decision.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]+|\d[\d.,:/-]*\d|\d")

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


def _novel_count(reply: str, baseline: str) -> int:
    """How many content words the reply has that the baseline does not.

    The absolute companion to ``_novelty``. A ratio answers "what share of this
    reply is new", which penalizes a correct reading that pads itself with the
    page's prose; this answers "did it bring anything back at all".
    """
    return len(_content_words(reply) - _content_words(baseline))


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


# Both prompts ask for the figure as a bold-labelled blockquote, so its presence
# is a direct answer to "did a figure come back" — the question novelty only ever
# approximated. The bold marker is what makes this safe to match: a page's own
# printed caption ("Figure 3: Layout of Civic Park...") is plain text and must
# NOT count, or a captioned page would suppress the pass that page most needs.
#
# The unbolded "Figure:" form is matched too, because the model drops the bold
# about as reliably as it drops the heading level — page 3's pass returned two
# maps' worth of correct labels as one run-on line of "Figure: ... Figure: ...".
# It is matched ONLY without a number, and that is the whole safety margin: a
# caption printed on the page is numbered ("Figure 3: Layout of Civic Park..."),
# and rewriting one into a > **Figure:** block would dress the document's own
# caption up as a reading of the picture — the precise confusion the figure pass
# exists to end, re-created by the formatter.
_FIGURE_LINE = re.compile(r"^\s*>?\s*(?:\*\*Figure\b|Figure\s*:)", re.I)
_FIGURE_MARK = re.compile(r">?\s*(?:\*\*Figure[^*\n]{0,24}\*\*:?|Figure\s*:)", re.I)

# Both prompts hand the model a template whose payload is an angle-bracket
# placeholder — "> **Figure:** <what it depicts. Name every box...>" — and it
# returns the brackets. Measured live: page 3 of the RFP came back with
# "**Figure:** <Map of the Hemisfair District with labeled streets and
# landmarks.>" on a page whose two maps carry street names, a route number and
# an acreage printed inside them. That is the template echoed, not a picture
# read, so it must not satisfy the figure-block gate.
#
# It is kept in the artifact (brackets stripped) rather than deleted: it is weak,
# not false, and if the second call then declines it is all the page has. The
# asymmetry is the one that governs routing throughout this module — a needless
# call costs tokens, a dropped figure costs a requirement.
_PLACEHOLDER = re.compile(r"^<[^<>]*>$", re.S)

# "This figure shows X" and "X" are the same sentence twice. The model restates
# its own description this way, and TRANSCRIPTION_PROMPT already bans the form
# outright ("Never write 'This slide shows...'"), so stripping the lead-in before
# comparing costs nothing and catches the near-duplicate the exact match misses.
_RESTATEMENT = re.compile(
    r"^(?:this|the)\s+(?:figure|image|picture|photo|diagram|map|drawing)\s+"
    r"(?:shows|depicts|displays|illustrates|represents)\s*(?:that\s+)?:?\s*",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _figure_payloads(text: str) -> list[str]:
    """The payload of every figure block on the page, in order.

    A single line may carry several: the model restates a whole description
    after its own block rather than stopping, so "> **Figure:** A. > **Figure:**
    A." arrives as one line holding the same figure twice.
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        if not _FIGURE_LINE.match(line):
            continue
        parts = _FIGURE_MARK.split(line)
        out.extend(p.strip().lstrip(">").strip() for p in parts[1:])
    return [p for p in out if p]


def _is_placeholder(payload: str) -> bool:
    return bool(_PLACEHOLDER.match(payload.strip()))


def _has_figure_block(text: str) -> bool:
    """True when a *described* figure came back, not merely a labelled one."""
    return any(not _is_placeholder(p) for p in _figure_payloads(text))


def _sentence_key(sentence: str) -> str:
    body = _RESTATEMENT.sub("", sentence.strip().strip("<>").strip())
    return re.sub(r"[^a-z0-9 ]+", "", body.lower()).strip()


def _dedupe_sentences(payload: str, seen: set[str]) -> str:
    """Drop sentences of ``payload`` already emitted elsewhere on this page."""
    kept = []
    for sentence in _SENTENCE_SPLIT.split(payload):
        key = _sentence_key(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(_RESTATEMENT.sub("", sentence.strip()))
    return " ".join(kept)


def _normalize_figures(text: str) -> str:
    """Rewrite the page's figure blocks: one per line, unbracketed, no repeats.

    Deduplication is page-wide and sentence-level, so a figure pass that re-serves
    what the transcription pass already said collapses into it rather than
    doubling it. Non-figure lines are passed through untouched.
    """
    if not _figure_payloads(text):
        return text

    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        if not _FIGURE_LINE.match(line):
            out.append(line)
            continue
        for part in _FIGURE_MARK.split(line)[1:]:
            payload = part.strip().lstrip(">").strip()
            if _is_placeholder(payload):
                payload = payload.strip()[1:-1].strip()
            payload = _dedupe_sentences(payload, seen)
            if payload:
                out.append(f"> **Figure:** {payload}")
    return "\n".join(out)


def _needs_vision(doc: render.Document, index: int, min_chars: int) -> bool:
    """Decide the route for one page.

    Most real briefs are digitally generated and carry a perfect embedded text
    layer. Running those through a vision model pays latency and tokens to
    lossily re-derive text we already have *exactly* — a 30-page digital RFP
    would cost 30 vision calls (~150s, past the UI's client timeout) instead of
    zero. Accuracy runs the same direction: the text layer is ground truth, the
    vision model is a guess at it.
    """
    if doc.kind == "image":
        return True  # no text layer exists to check
    if doc.kind == "slides":
        # A converted deck carries a PERFECT text layer — it is the real slide
        # text, not OCR — so every slide would take the free text route and the
        # deck feature would do nothing. That is the wrong trade here: a slide
        # is mostly picture, and the architecture diagram on it holds
        # constraints (on-prem, a named cloud, microservices) that exist
        # nowhere in its words. The text layer is still read and handed to the
        # worker, but only as the figure pass's novelty baseline.
        return True
    if len(doc.page_text(index).strip()) < min_chars:
        return True
    # A page can carry both real text and a diagram; the diagram holds
    # requirements the text layer misses, so it still earns a vision call.
    return doc.page_has_large_image(index)


class _FigureBudget:
    """Per-document ceiling on figure passes, shared across the page workers.

    Deliberately an object threaded through the call, not module state: the API
    serves concurrent uploads from one process, and a module-level counter would
    let the first brief of the day exhaust the budget for every brief after it.

    ``take`` is first-come rather than page-ordered — the pool hands pages out in
    order but they finish out of order, so under a cap the pages that get a pass
    are approximately, not exactly, the earliest ones. Exact ordering would mean
    serializing the gate behind page 1, which costs more than it buys.
    """

    def __init__(self, limit: int) -> None:
        self._left = max(0, limit)
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._left <= 0:
                return False
            self._left -= 1
            return True


def _figure_pass(
    page_no: int, image: bytes, vision: VisionClient, baseline: str = ""
) -> tuple[str, dict]:
    """Second, figure-only call. Returns ``(block, usage)``; block may be ''.

    Soft-fails to '' on any error: this runs only on a page whose first pass
    already produced usable text, so losing the figure block is a degradation,
    never a reason to fail the page.

    The reply is held to the same novelty bar as the first pass. FIGURE_PROMPT
    tells the model that a caption printed above or below a picture is not the
    picture's contents; measured on a live RFP page carrying two labelled aerial
    maps, it returned exactly those printed captions anyway, and every content
    word of the reply was already in the page's text layer. Appended unchecked,
    that puts a duplicated caption into the artifact dressed as a description of
    a picture nothing ever looked at. Dropping it leaves the page honestly
    without a figure block, which is the recoverable failure.

    ``baseline`` is the page's text layer where it has one and the transcription
    pass's own reply where it does not — see ``_render_page``. A scanned page
    reaches this check with a baseline either way, which is the point: the
    caption echo above was measured on a page whose captions are *printed on the
    page*, and rasterizing that page does not make them stop being printed.
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
    # Two ways to pass, because the ratio alone discards correct work: a reply
    # that reads the picture and then restates the page around it is diluted by
    # its own padding. Measured on page 5, two replies that both recovered the
    # map labels scored 0.154 and 0.065 — the second dropped for padding only.
    if (
        len(baseline.strip()) >= config.figure_pass_min_text_chars()
        and _novelty(text, baseline) < config.figure_pass_novelty()
        and _novel_count(text, baseline) < config.figure_pass_min_novel_words()
    ):
        log.info("page %d: figure pass echoed what was already read, dropped", page_no)
        return "", usage
    return _clamp_headings(text), usage


def _render_page(
    page_no: int,
    image: bytes,
    vision: VisionClient,
    text_layer: str = "",
    budget: _FigureBudget | None = None,
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

    markdown = _clamp_headings(markdown.strip())
    note = None

    # This page was routed to vision because it carries a figure. Two ways the
    # first call can have failed to describe it, and either one earns a second
    # call with a prompt whose only job is the picture:
    #
    # - No figure block came back at all. This is the direct test and it is the
    #   one that catches a page whose picture has a legend or table beside it:
    #   measured live, a colour-coded site plan whose legend the model read into
    #   a Markdown table scored 0.315 novelty, so novelty alone judged the call
    #   informative and the map went undescribed. Reading a legend is not
    #   describing a picture.
    # - A figure block came back, but the reply holds nothing the baseline did
    #   not already have, so the block is the page's own caption re-typed.
    #
    # Novelty is kept as the second trigger rather than replaced by the first:
    # it is what catches a well-formed block with no information in it.
    #
    # Only the SECOND trigger needs a baseline to measure against. Binding both
    # to one was the 2026-08-09 bug: a scanned page has no text layer, so the
    # whole gate was unreachable and a fully rasterized brief got zero figure
    # passes on every page. Replayed over the same RFP supplied both ways, the
    # pass could fire on 4 of 10 digital pages and 0 of 10 scanned ones, and the
    # scan's cover photo and site plans went undescribed while the digital copy's
    # were recovered. The failing document was the one MOST in need of the pass:
    # every page of it is a picture.
    #
    # Where there is no text layer the transcription reply stands in as the
    # baseline for the downstream echo check. It cannot serve as a novelty
    # baseline here — a reply compared against itself scores zero — which is why
    # that trigger keeps its precondition and this one does not.
    has_text = len(text_layer.strip()) >= config.figure_pass_min_text_chars()
    if not _has_figure_block(markdown) or (
        has_text and _novelty(markdown, text_layer) < config.figure_pass_novelty()
    ):
        if budget is not None and not budget.take():
            # Never silent: a capped page must not read as a page with no figure.
            note = "figure pass skipped: document budget"
            log.info("page %d: figure pass skipped, document budget spent", page_no)
        else:
            baseline = text_layer if has_text else markdown
            block, fig_usage = _figure_pass(page_no, image, vision, baseline)
            for k in _USAGE_KEYS:
                usage[k] = (usage.get(k) or 0) + (fig_usage.get(k) or 0)
            if block:
                markdown = f"{markdown}\n\n{block}"
                note = "figure pass"
                log.info("page %d: figure pass added %d chars", page_no, len(block))

    # Last, and over the combined text: a figure pass that re-served what the
    # transcription pass already said must collapse into it, not double it.
    markdown = _normalize_figures(markdown)

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

    # slides.open_brief, not render.open_document: a .pptx is converted to PDF
    # first. sha256 and source_file above are read from the ORIGINAL upload, so
    # the artifact names the file the visitor actually sent.
    with slides.open_brief(path) as doc:
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
                # page_markdown, not page_text: the routing test above and the
                # novelty baseline handed to the worker both want the RAW layer
                # they were tuned against, but the artifact wants the page's
                # ruled tables rebuilt as Markdown tables. See render.py.
                done.append(PageResult(page_no, doc.page_markdown(i).strip(), "text"))

    # Phase 2 — fan out the model calls only. One image per request: batching
    # was probed at 2-5 pages and rejected — see nim.py. The client is built
    # lazily, so a fully digital brief parses with no key configured.
    if pending:
        vision = vision or NimVisionClient()
        workers = max(1, min(config.concurrency(), len(pending)))
        # One budget per document, built here so it cannot outlive the request.
        budget = _FigureBudget(config.figure_pass_max_pages())
        with ThreadPoolExecutor(max_workers=workers) as pool:
            done.extend(
                pool.map(
                    lambda a: _render_page(a[0], a[1], vision, a[2], budget), pending
                )
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
