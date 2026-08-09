"""docparse.transcribe — routing, concurrency, failure accounting, assembly.

Zero network. ``FakeVisionClient`` is the direct analogue of
``tests/test_crawler.py``'s ``FakeFetcher``: it records its calls so the whole
page loop is assertable without a model. 40 RPM is exactly what makes a live
suite flaky (a 20-page parse is 20 requests), LLM output is not assertable at
temperature drift, and 5s/call makes the red-green loop unusable.
"""

from __future__ import annotations

import hashlib
import threading

import pytest

from stratpoint_rag.docparse import transcribe
from stratpoint_rag.docparse.transcribe import transcribe_document


A4_W, A4_H = 595, 842


@pytest.fixture
def serial(monkeypatch):
    """Pin page concurrency to 1.

    FakeVisionClient keys ``fail_on``/``reply`` to the order calls ARRIVE, but
    the pool starts every page at once, so arrival order is a race and "page 2"
    would mean whichever worker won the lock second. Tests that assert *which*
    page failed use this; the product itself is order-safe because
    transcribe_document sorts results by page number before accounting.
    """
    monkeypatch.setenv("DOCPARSE_CONCURRENCY", "1")


class FakeVisionClient:
    """Canned markdown + a call log. Mirrors FakeFetcher.calls.

    ``fail_on`` and a callable ``reply`` are indexed by call arrival order, so
    any test that depends on them matching page numbers must also use the
    ``serial`` fixture.
    """

    def __init__(self, reply="### Transcribed heading\n\nSome real body text.",
                 usage=None, fail_on=(), figure_reply="> **Figure:** A map."):
        self._reply = reply
        self._figure_reply = figure_reply
        self._usage = usage or {
            "prompt_tokens": 6431,
            "completion_tokens": 400,
            "total_tokens": 6831,
        }
        self._fail_on = set(fail_on)  # 1-based page numbers that raise
        self.calls = []
        self.user_turns = []
        self.threads = set()
        self._lock = threading.Lock()

    @property
    def figure_calls(self) -> list:
        return [c for c in self.calls if c[1] is transcribe.prompts.FIGURE_PROMPT]

    def describe(
        self, image_jpeg: bytes, prompt: str, user_turn: str = "Transcribe this page."
    ) -> tuple[str, dict]:
        with self._lock:
            n = len(self.calls) + 1
            self.calls.append((image_jpeg, prompt))
            self.user_turns.append(user_turn)
            self.threads.add(threading.get_ident())
        if n in self._fail_on:
            raise RuntimeError("endpoint exploded")
        # The figure pass is a second, differently-prompted call on the same
        # image; a test that exercises it needs its reply to differ.
        if prompt is transcribe.prompts.FIGURE_PROMPT:
            if callable(self._figure_reply):
                return self._figure_reply(n), dict(self._usage)
            return self._figure_reply, dict(self._usage)
        reply = self._reply(n) if callable(self._reply) else self._reply
        return reply, dict(self._usage)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def make_pdf(tmp_path):
    """Build a PDF whose pages carry a controlled number of text-layer chars."""

    def _make(name: str, *, page_texts: list[str]):
        import pymupdf

        doc = pymupdf.open()
        for body in page_texts:
            page = doc.new_page(width=A4_W, height=A4_H)
            if body:
                page.insert_textbox(
                    pymupdf.Rect(50, 50, A4_W - 50, A4_H - 50), body, fontsize=9
                )
        path = tmp_path / name
        doc.save(path)
        doc.close()
        return path

    return _make


@pytest.fixture
def scanned_pdf(tmp_path, make_pdf):
    """Image-only: rendered to pixmaps, so there is no text layer to route to."""
    import pymupdf

    src = pymupdf.open(make_pdf("src.pdf", page_texts=["Real text " * 40] * 3))
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=72)
        new = out.new_page(width=pix.width, height=pix.height)
        new.insert_image(pymupdf.Rect(0, 0, pix.width, pix.height), pixmap=pix)
    path = tmp_path / "scan.pdf"
    out.save(path)
    src.close()
    out.close()
    return path


# ── the cost saver: text-layer routing ──────────────────────────────────────


def test_digital_pdf_costs_zero_vision_calls(make_pdf):
    """A 30-page digital RFP must not pay 30 vision calls to re-derive text we
    already have exactly. The text layer is ground truth; vision is a guess."""
    path = make_pdf("digital.pdf", page_texts=["Requirements. " * 40] * 5)
    vision = FakeVisionClient()

    result = transcribe_document(path, vision=vision)

    assert vision.calls == []
    assert result.pages_parsed == 5
    assert result.pages_failed == []


def test_scanned_pdf_routes_every_page_to_vision(scanned_pdf):
    vision = FakeVisionClient()

    result = transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.calls) == 3
    assert result.pages_parsed == 3


def test_page_below_the_char_threshold_goes_to_vision(make_pdf, monkeypatch):
    monkeypatch.setenv("DOCPARSE_TEXT_LAYER_MIN_CHARS", "100")
    path = make_pdf("mixed.pdf", page_texts=["short", "Plenty of real text. " * 20])
    vision = FakeVisionClient()

    result = transcribe_document(path, vision=vision)

    assert len(vision.calls) == 1  # only the thin page
    assert "source: vision" in result.markdown.split("## Page 2")[0]
    assert "source: text" in result.markdown.split("## Page 2")[1]


def test_threshold_is_configurable(make_pdf, monkeypatch):
    monkeypatch.setenv("DOCPARSE_TEXT_LAYER_MIN_CHARS", "0")
    path = make_pdf("thin.pdf", page_texts=["hi"])
    vision = FakeVisionClient()

    transcribe_document(path, vision=vision)

    assert vision.calls == []  # nothing is below a threshold of 0


def test_page_with_text_and_a_diagram_still_uses_vision(tmp_path, make_pdf):
    """Architecture slides carry constraints that exist only as boxes and arrows."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_textbox(
        pymupdf.Rect(50, 50, A4_W - 50, 200), "Architecture overview. " * 20, fontsize=9
    )
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 500))
    pix.clear_with(180)
    page.insert_image(pymupdf.Rect(50, 300, 545, 800), pixmap=pix)
    path = tmp_path / "diagram.pdf"
    doc.save(path)
    doc.close()

    vision = FakeVisionClient()
    transcribe_document(path, vision=vision)

    assert len(vision.calls) == 1


def test_bare_image_skips_the_text_check_entirely(tmp_path):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 800, 600))
    pix.clear_with(220)
    path = tmp_path / "brief.png"
    pix.save(path)

    vision = FakeVisionClient()
    result = transcribe_document(path, vision=vision)

    assert len(vision.calls) == 1
    assert result.pages_total == 1


# ── the page cap ────────────────────────────────────────────────────────────


def test_pages_beyond_the_cap_are_not_parsed(make_pdf, monkeypatch):
    """Abuse guard and latency guard: 20 x 5s ~= 100s."""
    monkeypatch.setenv("DOCPARSE_MAX_PAGES", "3")
    path = make_pdf("long.pdf", page_texts=["Body text. " * 30] * 8)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.pages_total == 8
    assert result.pages_parsed == 3
    assert result.truncated is True
    assert "## Page 4" not in result.markdown


def test_untruncated_document_reports_truncated_false(make_pdf):
    path = make_pdf("short.pdf", page_texts=["Body text. " * 30] * 2)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.truncated is False


# ── failure handling: soft per page ─────────────────────────────────────────


def test_a_raising_page_is_recorded_and_the_rest_continue(scanned_pdf, serial):
    """Crawler precedent: per-page failures are soft, the run continues."""
    vision = FakeVisionClient(fail_on=(2,))

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.pages_failed == [2]
    assert result.pages_parsed == 2
    assert "## Page 1" in result.markdown
    assert "## Page 3" in result.markdown


def test_a_refusal_is_treated_as_a_failed_page(scanned_pdf, serial):
    """The model sometimes returns 'I'm unable to read this image.'"""
    vision = FakeVisionClient(
        reply=lambda n: "I'm unable to read this image." if n == 1 else "### Fine\n\nBody text here."
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.pages_failed == [1]
    assert "FAILED" in result.markdown.split("## Page 2")[0]


def test_empty_output_is_treated_as_a_failed_page(scanned_pdf, serial):
    vision = FakeVisionClient(reply=lambda n: "" if n == 2 else "### Fine\n\nBody text here.")

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.pages_failed == [2]


def test_failed_pages_are_reported_in_order(scanned_pdf):
    """Concurrency must not scramble the accounting."""
    vision = FakeVisionClient(fail_on=(1, 2, 3))

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.pages_failed == [1, 2, 3]
    assert result.pages_parsed == 0


def test_unopenable_file_aborts_rather_than_soft_failing(tmp_path):
    """Only setup problems abort."""
    from stratpoint_rag.docparse import render

    path = tmp_path / "deck.pptx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)

    with pytest.raises(render.UnsupportedDocument):
        transcribe_document(path, vision=FakeVisionClient())


# ── assembly: Python owns the wrapper, never the LLM ────────────────────────


def test_page_headings_are_emitted_by_python_at_level_two(scanned_pdf):
    """Page numbering must be exact because pages_failed accounting depends on it."""
    vision = FakeVisionClient(reply="### Model heading\n\nModel body text.")

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "## Page 1" in result.markdown
    assert "## Page 2" in result.markdown
    assert "## Page 3" in result.markdown
    assert "\n# " not in result.markdown  # no h1 anywhere


def test_model_output_is_pasted_verbatim_beneath_the_wrapper(scanned_pdf):
    vision = FakeVisionClient(reply="### Functional Requirements\n\n| ID | Requirement |")

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "### Functional Requirements\n\n| ID | Requirement |" in result.markdown


def test_frontmatter_carries_provenance(make_pdf):
    path = make_pdf("brief.pdf", page_texts=["Body text. " * 30] * 2)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    result = transcribe_document(path, vision=FakeVisionClient())
    head = result.markdown.split("---")[1]

    assert "source_file: brief.pdf" in head
    assert f"sha256: {expected}" in head
    assert "pages_total: 2" in head
    assert "pages_parsed: 2" in head
    assert "pages_failed: []" in head


def test_frontmatter_lists_failed_pages(scanned_pdf, serial):
    vision = FakeVisionClient(fail_on=(2,))

    result = transcribe_document(scanned_pdf, vision=vision)
    head = result.markdown.split("---")[1]

    assert "pages_failed: [2]" in head


def test_sha256_is_exposed_for_the_upload_cache(make_pdf):
    path = make_pdf("cached.pdf", page_texts=["Body text. " * 30])

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_no_timestamp_is_stamped_inside_the_module(make_pdf):
    """Determinism rule: the clock lives at the API layer, as with crawled_at."""
    path = make_pdf("stable.pdf", page_texts=["Body text. " * 30])

    first = transcribe_document(path, vision=FakeVisionClient())
    second = transcribe_document(path, vision=FakeVisionClient())

    assert first.markdown == second.markdown


# ── LLMOps: workers return usage, the parent accumulates ────────────────────


def test_usage_is_summed_across_pages(scanned_pdf):
    vision = FakeVisionClient(
        usage={"prompt_tokens": 6431, "completion_tokens": 400, "total_tokens": 6831}
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.usage["prompt_tokens"] == 3 * 6431
    assert result.usage["completion_tokens"] == 3 * 400
    assert result.usage["total_tokens"] == 3 * 6831


def test_add_usage_is_never_called_from_a_worker_thread(scanned_pdf, monkeypatch):
    """llmops/usage.py is a threading.local() that assumes one request per
    thread. add_usage() inside a worker accumulates into an accumulator the
    request thread never reads, so ~129k prompt tokens per 20-page brief would
    silently vanish from /metrics."""
    recording_threads = []

    def spy(usage):
        recording_threads.append(threading.get_ident())

    monkeypatch.setattr(transcribe.llmops, "add_usage", spy)
    monkeypatch.setenv("DOCPARSE_CONCURRENCY", "3")
    vision = FakeVisionClient()

    transcribe_document(scanned_pdf, vision=vision)

    assert recording_threads, "usage was never recorded at all"
    assert set(recording_threads) == {threading.get_ident()}
    assert vision.threads.isdisjoint({threading.get_ident()})


def test_usage_reaches_the_thread_local_accumulator(scanned_pdf):
    from stratpoint_rag import llmops

    llmops.reset_usage()
    transcribe_document(scanned_pdf, vision=FakeVisionClient())

    popped = llmops.pop_usage()
    assert popped is not None
    assert popped["prompt_tokens"] == 3 * 6431


def test_text_only_document_records_no_usage(make_pdf):
    path = make_pdf("digital.pdf", page_texts=["Body text. " * 30] * 2)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.usage["total_tokens"] == 0


# ── the prompt reaches the model ────────────────────────────────────────────


def test_omitting_the_client_uses_the_production_one(scanned_pdf, monkeypatch):
    """Injection is for tests; production callers just don't pass one."""
    from stratpoint_rag.docparse.nim import NimVisionClient

    built = []
    monkeypatch.setattr(
        transcribe, "NimVisionClient", lambda: built.append(1) or FakeVisionClient()
    )

    transcribe_document(scanned_pdf)

    assert built, "expected transcribe_document to construct a NimVisionClient"
    assert issubclass(NimVisionClient, object)


def test_text_only_document_needs_no_vision_client_at_all(make_pdf, monkeypatch):
    """A digital RFP must parse with no key configured and no network."""
    monkeypatch.delenv("NVIDIA_VISION_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    path = make_pdf("digital.pdf", page_texts=["Requirements. " * 40] * 3)

    result = transcribe_document(path)

    assert result.pages_parsed == 3
    assert result.pages_via_vision == 0


def test_each_page_is_a_separate_call_carrying_jpeg_bytes(scanned_pdf):
    """One image per request — the endpoint hard-refuses two with HTTP 400."""
    vision = FakeVisionClient()

    transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.calls) == 3
    for image, prompt in vision.calls:
        assert image[:3] == b"\xff\xd8\xff"  # jpeg
        assert prompt == transcribe.prompts.TRANSCRIPTION_PROMPT


# ── degeneration loops ──────────────────────────────────────────────────────
#
# Regression: an RFP cover page (one photo, four lines of text) came back with
# eight distinct lines repeated 40x, 2,048 completion tokens, truncated at the
# ceiling mid-loop. config.FREQUENCY_PENALTY is the primary fix; this collapse
# is the deterministic backstop, and it must keep the real transcription that
# preceded the loop.


def test_a_runaway_repetition_loop_is_collapsed(scanned_pdf, serial):
    real = "### Request for Proposal\n\nHemisfair Augmented Reality Project."
    loop = "\n\n".join(["**Request for Proposal**"] * 40)
    vision = FakeVisionClient(reply=f"{real}\n\n{loop}")

    result = transcribe_document(scanned_pdf, vision=vision)

    body = result.markdown
    assert body.count("**Request for Proposal**") == 3 * 3  # 3 pages x cap of 3
    # the genuine transcription that preceded the loop survives
    assert "Hemisfair Augmented Reality Project." in body
    assert result.pages_failed == []
    assert result.pages_parsed == 3


def test_a_collapsed_page_says_so_in_its_provenance_marker(scanned_pdf, serial):
    """A page the pipeline had to repair must not read as a clean parse."""
    vision = FakeVisionClient(reply="\n\n".join(["Same line."] * 30))

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "| collapsed 27 repeated lines -->" in result.markdown


def test_an_ordinary_page_is_left_byte_identical(scanned_pdf, serial):
    """Real pages repeat lines a little; only a genuine loop may be touched."""
    reply = (
        "### Insurance\n\n"
        "| Coverage | Limit |\n| --- | --- |\n"
        "| Bodily Injury | $1,000,000 |\n"
        "| Property Damage | $1,000,000 |\n"
        "| Umbrella | $1,000,000 |"
    )
    vision = FakeVisionClient(reply=reply)

    result = transcribe_document(scanned_pdf, vision=vision)

    assert reply in result.markdown
    assert "collapsed" not in result.markdown


def test_a_reply_that_is_nothing_but_loop_fails_the_page(scanned_pdf, serial):
    """Collapsing can leave too little to be a transcription — that is a failure,
    not a three-line page silently presented as the client's document."""
    vision = FakeVisionClient(reply="\n\n".join(["Hi."] * 50))

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.pages_failed == [1, 2, 3]
    assert "response too short to be a transcription" in result.markdown


def test_collapse_preserves_order_and_keeps_the_first_occurrences():
    text = "A\n\nB\n\nA\n\nC\n\nA\n\nA\n\nA\n\nD"
    out, dropped = transcribe._collapse_repetition(text)

    assert dropped == 2
    assert [ln for ln in out.splitlines() if ln.strip()] == ["A", "B", "A", "C", "A", "D"]


# ── the figure pass ─────────────────────────────────────────────────────────
#
# Regression: an RFP page carrying two labelled aerial maps was routed to vision
# and came back holding nothing its own text layer did not already have — the
# maps' printed labels ("Civic Park - 2023", "Tower Park - 2025") were lost, and
# the reply's captions were the page's own printed captions, re-typed. Cause is
# NOT any single rule in TRANSCRIPTION_PROMPT: ablating each of its ten bullets
# left the page unchanged, while dropping the system prompt entirely fixed it.
# The prompt's aggregate posture is "text transcriber", and that page is where
# the posture is fully satisfiable without looking at the pictures.


@pytest.fixture
def figure_page_pdf(tmp_path):
    """One page: a substantial text layer AND an image big enough to route to
    vision. This is the shape the bug needs — a scan or a bare image cannot
    reproduce it, because novelty is meaningless without a text layer."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_textbox(
        pymupdf.Rect(50, 400, A4_W - 50, A4_H - 50),
        "The green space in the garden and the park covers thirteen acres "
        "downtown. Donor sponsored trees have already been planted there, so "
        "launching a pilot programme this year is necessary. " * 3,
        fontsize=10,
    )
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 900, 500))
    pix.clear_with(170)
    page.insert_image(pymupdf.Rect(50, 50, 500, 300), pixmap=pix)

    path = tmp_path / "figure_page.pdf"
    doc.save(path)
    doc.close()
    return path


# A reply that merely echoes the page's own text layer — the measured failure.
_ECHO = (
    "The green space in the garden and the park covers thirteen acres "
    "downtown. Donor sponsored trees have already been planted there, so "
    "launching a pilot programme this year is necessary."
)


def test_a_vision_call_that_added_nothing_triggers_the_figure_pass(figure_page_pdf):
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply="> **Figure:** Civic Park - 2023, Tower Park - 2025.",
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "Civic Park - 2023" in result.markdown
    assert "| figure pass -->" in result.markdown
    # the transcription itself is kept, not replaced
    assert "thirteen acres" in result.markdown


def test_the_figure_pass_uses_its_own_prompt_and_user_turn(figure_page_pdf):
    vision = FakeVisionClient(reply=_ECHO)

    transcribe_document(figure_page_pdf, vision=vision)

    assert vision.calls[0][1] is transcribe.prompts.TRANSCRIPTION_PROMPT
    assert vision.calls[1][1] is transcribe.prompts.FIGURE_PROMPT
    assert vision.user_turns[1] == transcribe.prompts.FIGURE_USER_TURN


def test_a_vision_call_that_added_real_content_pays_for_no_second_call(figure_page_pdf):
    """The gate is the whole point: pages 1, 3 and 6 of the live RFP worked and
    must not pay a second call each."""
    vision = FakeVisionClient(
        reply=_ECHO + "\n\n> **Figure:** Sycamores flank the promenade, "
        "twenty feet apart east-west, eighteen north-south, marked by yellow dots."
    )

    transcribe_document(figure_page_pdf, vision=vision)

    assert vision.figure_calls == []


def test_a_declined_figure_pass_appends_nothing(figure_page_pdf):
    """The model declines in prose, not a sentinel. A decline appended verbatim
    would put 'There are no pictures on this page.' into the artifact."""
    vision = FakeVisionClient(
        reply=_ECHO, figure_reply="There are no pictures on this page."
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "no pictures on this page" not in result.markdown
    assert "figure pass" not in result.markdown


def test_a_scan_never_pays_for_a_figure_pass(scanned_pdf):
    """No text layer means novelty is trivially 1.0 and says nothing. Guessing
    there would put a second call on every page of every scanned brief."""
    vision = FakeVisionClient(reply=_ECHO)

    transcribe_document(scanned_pdf, vision=vision)

    assert vision.figure_calls == []


def test_figure_pass_tokens_are_accounted(figure_page_pdf):
    """Two calls, two bills. Usage is summed on the request thread — a figure
    pass whose tokens vanish from /metrics is the hop-1 threading bug again."""
    usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    vision = FakeVisionClient(reply=_ECHO, usage=usage)

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.calls) == 2
    assert result.usage["total_tokens"] == 220
    assert result.usage["prompt_tokens"] == 200


def test_a_failing_figure_pass_still_keeps_the_page(figure_page_pdf):
    """The first pass produced usable text; losing the figure block is a
    degradation, never a reason to fail the page."""

    def boom(n):
        raise RuntimeError("endpoint exploded")

    vision = FakeVisionClient(reply=_ECHO, figure_reply=boom)

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert result.pages_failed == []
    assert "thirteen acres" in result.markdown


def test_novelty_scores_an_echo_near_zero_and_new_content_high():
    layer = "The green space in the garden covers thirteen acres downtown."

    assert transcribe._novelty(layer, layer) == 0.0
    assert transcribe._novelty("Civic Park 2023 Tower Park 2025", layer) > 0.9
    # Reordered and re-punctuated, but still only the layer's own words: the
    # measured failure looked exactly like this, not like a verbatim copy.
    assert transcribe._novelty("Thirteen acres downtown; green garden.", layer) == 0.0
    # Scaffolding the prompt itself supplies is not evidence the model looked.
    assert transcribe._novelty("**Figure:** the table on this page", layer) == 0.0
