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


# The default reply carries a figure block on purpose. Since 2026-08-09 a page
# whose reply has no described figure earns a second, figure-only call — true of
# every page of a scanned brief, since a scan has no text layer to fall back on.
# Most tests in this file are about routing, failure accounting or usage, and a
# second call perturbs all three (it doubles usage, and FakeVisionClient's
# arrival-order indexing shifts under it). A default that satisfies the gate
# keeps those tests measuring their own subject. Tests about the figure pass
# itself pass an explicit reply.
_DEFAULT_REPLY = (
    "### Transcribed heading\n\nSome real body text.\n\n"
    "> **Figure:** An aerial map of the district, US-281 and Market Street."
)


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

    def __init__(self, reply=_DEFAULT_REPLY,
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

    # Explicitly figure-less: the second assertion below is about what happens
    # when a page routed for its diagram comes back without one.
    vision = FakeVisionClient(reply="### Architecture\n\nThe platform is hosted.")
    result = transcribe_document(path, vision=vision)

    # The subject here is the ROUTE, not the call count: this page must not take
    # the free text-layer path. It also earns a figure pass, because the reply
    # carries no figure block and a page routed for a diagram that came back
    # without one is precisely what that second call is for.
    assert result.pages_via_vision == 1
    assert vision.calls[0][1] is transcribe.prompts.TRANSCRIPTION_PROMPT
    assert len(vision.figure_calls) == 1


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
    # The good pages answer with a figure block so they cost one call each —
    # otherwise their figure passes shift the arrival index this lambda keys on.
    vision = FakeVisionClient(reply=lambda n: "" if n == 2 else _DEFAULT_REPLY)

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
    """One image per request — batching 2-5 pages per call was probed and
    rejected; recall fell 1.000 -> 0.63 at 4 pages. See nim.py."""
    vision = FakeVisionClient()

    transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.calls) == 3
    for image, prompt in vision.calls:
        assert image[:3] == b"\xff\xd8\xff"  # jpeg
        assert prompt == transcribe.prompts.TRANSCRIPTION_PROMPT


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


def test_a_scan_with_no_figure_block_still_gets_a_figure_pass(scanned_pdf):
    """Retargeted 2026-08-09. This asserted ``figure_calls == []`` on the
    reasoning that novelty is trivially 1.0 without a text layer — true of
    *novelty*, but it was written as a precondition on the whole gate, and the
    figure-block trigger needs no text layer at all. Measured consequence: a
    fully rasterized 10-page RFP got zero figure passes on all ten pages, and
    its cover photo and two site plans went undescribed while the digital copy
    of the same document described them.
    """
    vision = FakeVisionClient(reply=_ECHO)

    transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.figure_calls) == 3


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


# Regression, 2026-08-09, same live RFP, two pages the novelty gate alone got
# wrong. Page 6 carries a colour-coded site plan whose legend sits beside it; the
# transcription pass read the legend into a Markdown table and scored 0.315
# novelty, so the gate judged the call informative and skipped the figure pass —
# the map itself was never described. Page 5 fired the pass and got the page's
# own printed captions back, which were appended as though they were a figure.
#
# Novelty answers "did the reply add words", which is only a proxy for "did the
# model look at the picture", and a legend or table beside the figure breaks the
# proxy. The direct question is whether a figure block came back at all.


def test_a_reply_with_no_figure_block_triggers_the_pass_despite_high_novelty(
    figure_page_pdf,
):
    """Page 6: the novelty came entirely from a legend read off the map."""
    legend = (
        "| Colour | Description |\n"
        "| :--- | :--- |\n"
        "| Red | Oaks, Elms and Sycamores |\n"
        "| Yellow | Sycamores along the promenade |\n"
        "| Blue | Burr Oaks beside the lawn |"
    )
    vision = FakeVisionClient(
        reply=_ECHO + "\n\n" + legend,
        figure_reply="> **Figure:** Civic Park layout, Zocalo, Monterrey Oaks.",
    )

    assert transcribe._novelty(_ECHO + "\n\n" + legend, _ECHO) > 0.10, (
        "fixture must reproduce the high-novelty condition, else the test is vacuous"
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "Zocalo" in result.markdown
    assert "| figure pass -->" in result.markdown
    # the legend the transcription pass did read is kept, not replaced
    assert "Burr Oaks beside the lawn" in result.markdown


def test_a_figure_pass_that_only_echoes_the_captions_appends_nothing(figure_page_pdf):
    """Page 5: the model returned the page's own printed captions.

    FIGURE_PROMPT says a caption is not the picture's contents; the model ignores
    that, and an unvalidated echo puts a duplicated caption into the artifact
    dressed as a figure description. Absent beats confidently wrong.
    """
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply="> **Figure:** The garden and the park, thirteen acres downtown.",
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "figure pass" not in result.markdown
    assert result.markdown.count("thirteen acres") == 1
    assert result.pages_failed == []


# Both prompts hand the model a template — "> **Figure:** <what it depicts...>"
# — and it returns the angle brackets, filled with a caption-level restatement.
# Measured live on the same RFP: page 3 came back with
# "**Figure:** <Map of the Hemisfair District with labeled streets and
# landmarks.>" on a page whose two maps carry street names, a route number and
# an acreage printed inside them. That satisfied a naive figure-block test and
# scored 17.7% novelty, so it fell through both triggers and the maps went
# unread. A block in the template's own placeholder form is the template echoed,
# not a picture described.


def test_a_placeholder_figure_block_does_not_satisfy_the_gate(figure_page_pdf):
    """Page 3: high novelty AND a figure block, but the block is the template."""
    placeholder = "**Figure:** <Map of the district with labeled streets.>"
    reply = (
        _ECHO + "\n\nCivic Park hosts Monterrey Oaks beside the Zocalo "
        "promenade.\n\n" + placeholder
    )
    vision = FakeVisionClient(
        reply=reply,
        figure_reply="> **Figure:** US-281, Market Street, nineteen acres.",
    )

    assert transcribe._novelty(reply, _ECHO) > 0.10, "test must isolate the placeholder"

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "US-281" in result.markdown
    # Weak, not false: kept in the artifact, with the template brackets stripped.
    assert "Map of the district with labeled streets." in result.markdown
    assert "<Map of the district" not in result.markdown


def test_a_repeated_figure_description_is_emitted_once(figure_page_pdf):
    """Page 1: the model restated its whole description on the same line."""
    desc = (
        "A tree with a colorful mural behind it. The sky is clear and blue."
    )
    vision = FakeVisionClient(
        reply=_ECHO, figure_reply=f"> **Figure:** {desc} > **Figure:** {desc}"
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert result.markdown.count("colorful mural") == 1
    assert result.markdown.count("clear and blue") == 1


def test_a_restated_figure_sentence_is_dropped(figure_page_pdf):
    """The same sentence re-served as commentary is still the same sentence."""
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply=(
            "> **Figure:** A mural of swirling blue patterns. "
            "This figure shows a mural of swirling blue patterns."
        ),
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert result.markdown.count("swirling blue patterns") == 1
    assert "This figure shows" not in result.markdown


def test_unbolded_figure_markers_are_split_and_formatted(figure_page_pdf):
    """Page 3: the figure pass returns the right labels in the wrong shape.

    Two maps arrive as one run-on line with plain "Figure:" markers and no
    blockquote. The content is what the pass exists for; the shape is Python's.
    """
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply=(
            "Figure: South Alamo Street US-281 Market Street "
            "Figure: Yanaguana Garden Tower Park Currently dedicated parkland"
        ),
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert "> **Figure:** South Alamo Street US-281 Market Street" in result.markdown
    assert (
        "> **Figure:** Yanaguana Garden Tower Park Currently dedicated parkland"
        in result.markdown
    )


def test_a_printed_numbered_caption_is_never_rewritten_as_a_figure_block():
    """The document's OWN caption is transcribed content, not a description.

    "Figure 3: Layout of Civic Park..." is printed on the page; rewriting it into
    a > **Figure:** block would dress the page's caption up as a reading of the
    picture -- the exact confusion the figure pass exists to end.
    """
    body = (
        "| Blue | Burr Oaks next to lawn |\n\n"
        "Figure 3: Layout of Civic Park showing tree variety placement.\n\n"
        "> **Figure:** SHEET L1.007 REFERENCE MASTER TREE PRESERVATION PERMIT"
    )

    out = transcribe._normalize_figures(body)

    assert "Figure 3: Layout of Civic Park showing tree variety placement." in out
    assert "**Figure:** Layout of Civic Park" not in out
    assert "> **Figure:** SHEET L1.007 REFERENCE MASTER TREE PRESERVATION PERMIT" in out


def test_novelty_scores_an_echo_near_zero_and_new_content_high():
    layer = "The green space in the garden covers thirteen acres downtown."

    assert transcribe._novelty(layer, layer) == 0.0
    assert transcribe._novelty("Civic Park 2023 Tower Park 2025", layer) > 0.9
    # Reordered and re-punctuated, but still only the layer's own words: the
    # measured failure looked exactly like this, not like a verbatim copy.
    assert transcribe._novelty("Thirteen acres downtown; green garden.", layer) == 0.0
    # Scaffolding the prompt itself supplies is not evidence the model looked.
    assert transcribe._novelty("**Figure:** the table on this page", layer) == 0.0


# ── heading clamp ───────────────────────────────────────────────────────────
#
# Regression: nemotron ignores the prompt's "### or deeper ONLY" rule in about
# half of runs, emitting "# General Information", "## Deliverables" and
# "## Site Information". A ## in a page body collides with the `## Page N`
# wrapper Python owns, and pages_failed accounting depends on that wrapper
# being unambiguous. The prompt rule stays as the nudge; this is the backstop.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("# General Information", "### General Information"),
        ("## Deliverables", "### Deliverables"),
        ("### Site Information", "### Site Information"),
        ("#### Sub point", "#### Sub point"),
        ("Body text\n\n## Deliverables\n\nMore body",
         "Body text\n\n### Deliverables\n\nMore body"),
    ],
)
def test_clamp_demotes_only_shallow_headings(raw, expected):
    assert transcribe._clamp_headings(raw) == expected


def test_clamp_leaves_hashes_that_are_not_headings_alone():
    """A page may print "Item # 4" or "#1 priority"; neither is a heading, and
    CommonMark requires the space, so the clamp requires it too."""
    text = "Ref # 4 applies.\n#1 priority\nTotal: 5 # of units"

    assert transcribe._clamp_headings(text) == text


def test_clamp_preserves_relative_hierarchy_within_a_page():
    """Both levels land on ###, which flattens — that is accepted. What must not
    happen is a body heading outranking the page wrapper."""
    out = transcribe._clamp_headings("# Top\n\n## Second\n\n### Third")

    assert out == "### Top\n\n### Second\n\n### Third"


def test_a_shallow_heading_never_reaches_the_artifact(scanned_pdf):
    """The wrapper must remain the only ## in the document."""
    vision = FakeVisionClient(reply="## Deliverables\n\nThe scope of work is:")

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "### Deliverables" in result.markdown
    shallow = [
        ln for ln in result.markdown.splitlines()
        if ln.startswith("# ") or (ln.startswith("## ") and not ln.startswith("## Page "))
    ]
    assert shallow == []


def test_the_figure_block_is_clamped_too(figure_page_pdf):
    """The figure pass is a second model call and obeys the prompt no better.

    ``_ECHO`` is the module-level reply that scores near-zero novelty against
    ``figure_page_pdf``'s text layer — it is what makes the figure pass fire at
    all. Any other reply skips the second call and this test would pass
    vacuously."""
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply="## Figure\n\n> **Figure:** An aerial map of the district.",
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert "### Figure" in result.markdown
    assert "\n## Figure" not in result.markdown


# Regression, 2026-08-09. Reported against one live artifact: "when the pdf
# itself is full images, the actual images are not detected and described".
#
# Cause was not the model. The figure-pass gate carried a precondition —
# len(text_layer) >= figure_pass_min_text_chars — on BOTH its triggers, and a
# scanned page has no text layer, so the pass was unreachable by construction
# on every page of every scanned brief. Replayed offline over the same 10-page
# RFP supplied twice: the figure pass could fire on 4 of 10 digital pages and
# 0 of 10 rasterized ones. Only the novelty trigger needs a text layer.
#
# What replaces it on a scan is the transcription pass's own reply: it is
# measured at 1.000 content-word recall against the digital copy's text layer,
# so it is the best available baseline for "did the second call add anything".


# _ECHO alone is 184 chars, under figure_pass_min_text_chars — as a *text layer*
# it rides along with the fixture's repeated body, but as a stand-in baseline it
# is the reply itself and would fall under the floor. A real scanned page
# transcribes to well over 200 chars; these tests say so explicitly rather than
# passing by an accident of fixture length.
_SCAN_ECHO = (
    _ECHO + " Trees are planted as tributes to memorialize a loved one who has "
    "passed, or to honour someone living, and each carries a donor plaque."
)


def test_a_scan_whose_reply_carries_a_figure_block_pays_no_second_call(scanned_pdf):
    """The cost guard has to survive losing the text layer, or every page of
    every scan pays twice."""
    vision = FakeVisionClient(
        reply=_ECHO + "\n\n> **Figure:** Two aerial maps, US-281 and Market Street."
    )

    transcribe_document(scanned_pdf, vision=vision)

    assert vision.figure_calls == []


def test_on_a_scan_the_figure_pass_is_validated_against_the_transcription(
    scanned_pdf, serial
):
    """Page 5's protection, restored without a text layer.

    The model returns the page's printed captions instead of the picture's
    contents. On a digital page that is caught by comparing against the text
    layer; on a scan the transcription reply holds the same captions and serves
    the same purpose. Unvalidated, a re-typed caption enters the artifact
    dressed as a reading of a picture nothing looked at.
    """
    vision = FakeVisionClient(
        reply=_SCAN_ECHO,
        figure_reply="> **Figure:** The garden and the park, thirteen acres downtown.",
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.figure_calls) == 3
    assert "figure pass -->" not in result.markdown
    assert result.pages_failed == []


def test_a_novel_figure_pass_on_a_scan_is_kept(scanned_pdf, serial):
    """The other side of the same check — the case the whole fix exists for."""
    vision = FakeVisionClient(
        reply=_SCAN_ECHO,
        figure_reply="> **Figure:** SHEET L1.007, SHEET L1.008, permit AP #A12295605.",
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "SHEET L1.007" in result.markdown
    assert "| figure pass -->" in result.markdown


# The cap. Firing on every figure-less page is right for a 10-page brief and
# unaffordable at the 40-page ceiling, where it would double a parse that
# already spends the whole of NIM's 40 RPM quota.


def test_the_figure_pass_is_capped_per_document(scanned_pdf, monkeypatch, serial):
    monkeypatch.setenv("DOCPARSE_FIGURE_PASS_MAX_PAGES", "1")
    vision = FakeVisionClient(reply=_ECHO)

    transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.figure_calls) == 1


def test_a_page_skipped_for_budget_says_so_in_its_provenance(
    scanned_pdf, monkeypatch, serial
):
    """A silent cap reads as 'this page had no figure'. It must read as
    'nobody looked'."""
    monkeypatch.setenv("DOCPARSE_FIGURE_PASS_MAX_PAGES", "1")
    vision = FakeVisionClient(reply=_ECHO)

    result = transcribe_document(scanned_pdf, vision=vision)

    assert result.markdown.count("figure pass skipped: document budget") == 2
    assert result.pages_failed == []


def test_the_budget_is_per_document_not_per_process(scanned_pdf, monkeypatch, serial):
    """A module-level counter would exhaust on the first upload and leave every
    later one in the same API process without a figure pass at all."""
    monkeypatch.setenv("DOCPARSE_FIGURE_PASS_MAX_PAGES", "2")

    first = FakeVisionClient(reply=_ECHO)
    transcribe_document(scanned_pdf, vision=first)
    second = FakeVisionClient(reply=_ECHO)
    transcribe_document(scanned_pdf, vision=second)

    assert len(first.figure_calls) == 2
    assert len(second.figure_calls) == 2


# ── prompt/parser contract ──────────────────────────────────────────────────
#
# The prompts hand the model an output shape and transcribe.py parses that shape
# back out. Nothing but these tests couples the two, and the coupling is easy to
# break silently: a prompt edit that changes the marker leaves every regex here
# matching nothing, the figure gate permanently unsatisfied, and the failure
# looks like "the model stopped describing figures" rather than like an edit.


def test_the_figure_prompts_output_form_is_what_the_parser_matches():
    """Both prompts show the model a blockquote; _FIGURE_LINE must accept it."""
    for prompt in (transcribe.prompts.FIGURE_PROMPT,
                   transcribe.prompts.TRANSCRIPTION_PROMPT):
        shown = [
            ln for ln in prompt.splitlines()
            if ln.lstrip().startswith("> **Figure:**")
        ]
        assert shown, f"prompt no longer shows a figure blockquote:\n{prompt}"
        for line in shown:
            assert transcribe._FIGURE_LINE.match(line), line
            assert transcribe._FIGURE_MARK.search(line), line


def test_the_declines_wording_is_one_the_decline_matcher_accepts():
    """FIGURE_PROMPT dictates an exact sentence; NO_FIGURES_MARKERS must cover
    it, or a decline is appended to the artifact as though it were a figure."""
    sentence = "There are no pictures on this page."
    assert sentence in transcribe.prompts.FIGURE_PROMPT
    assert any(m in sentence.lower() for m in transcribe.prompts.NO_FIGURES_MARKERS)


def test_the_figure_prompt_still_asks_for_the_words_inside_the_picture():
    """The one instruction the pass exists for. It currently sits inside the
    prompt's own "> **Figure:** <...>" template; a 2026-08-09 probe tested
    hoisting it into a numbered step and found NO difference on a rested
    endpoint (8/8 both arms), so the shape is not load-bearing — but the
    directive's presence is."""
    prompt = transcribe.prompts.FIGURE_PROMPT
    assert [
        ln for ln in prompt.splitlines()
        if "printed" in ln and "inside" in ln.lower()
    ], "the copy-every-word-inside-the-picture directive is gone"


# Regression, 2026-08-09. Page 5 of the live RFP carried two aerial maps and
# never got a figure block, across every run of two sessions. The figure pass
# was not failing: probed directly on that page's production raster, 16 of 16
# replies read "Civic Park - 2023", "Tower Park - 2025" and "Yanaguana Garden -
# 2015" off the map. All 16 were then DROPPED by the novelty check at 0.048.
#
# _WORD_RE required a leading letter, so a number was never a content word. The
# map's labels are place names the page's prose already uses plus the years, and
# with the years invisible a correct reading of the picture scored as a pure
# echo of the text layer. The most decisive evidence that the model looked --
# numbers printed inside the picture -- was the one thing the metric could not
# see. Counting numbers takes those replies to 0.167 and keeps 16 of 16, and
# moves no page's trigger decision on the 10-page corpus.


def test_numbers_printed_inside_a_picture_count_as_content():
    assert transcribe._content_words("Civic Park - 2023") == {"civic", "park", "2023"}


def test_a_figure_pass_whose_only_new_information_is_numbers_is_kept(figure_page_pdf):
    """The page-5 shape in miniature: every WORD of the reply is already in the
    text layer and only the numbers are new."""
    vision = FakeVisionClient(
        reply=_ECHO,
        figure_reply="> **Figure:** The garden 2023, the park 2025, downtown 2015.",
    )

    result = transcribe_document(figure_page_pdf, vision=vision)

    assert len(vision.figure_calls) == 1
    assert "2023" in result.markdown and "2015" in result.markdown
    assert "| figure pass -->" in result.markdown


def test_a_reply_whose_numbers_are_already_known_is_still_dropped(scanned_pdf, serial):
    """The valve must keep working: numbers count on BOTH sides of the ratio, so
    re-serving figures the page already prints buys nothing. This is what keeps
    a table misread as a picture out of the artifact — measured live on a page
    of insurance limits, 0.045 before this change and 0.042 after.

    Uses the scanned route deliberately: there the baseline is the transcription
    reply, so the test controls both sides of the comparison.
    """
    vision = FakeVisionClient(
        reply=_SCAN_ECHO + " The budget range is 50000 to 100000 for the work.",
        figure_reply="> **Figure:** 50000 100000 garden park thirteen acres downtown.",
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert len(vision.figure_calls) == 3
    assert "figure pass -->" not in result.markdown


# Regression, 2026-08-09, the other half of page 5. Novelty is a RATIO over the
# whole reply, so a reply that reads the picture correctly AND then pads itself
# with the page's prose is punished for the padding. Traced live on the scanned
# route, four reps of the same page: two replies read the maps, and one of those
# was dropped at 0.065 because it restated the page body around the labels.
#
# The question the check exists to ask is "did this reply bring anything back",
# which is a count, not a proportion. Measured novel-content-word counts on the
# corpus: a reply that read the maps carries 4, a table misread as a figure
# carries 1, a pure caption echo carries 0. A floor of 3 sits in that gap with
# margin on both sides, the same way 0.10 sits between 1.6% and 17.7%.

# Long enough that four novel tokens cannot clear the ratio on their own.
_PADDED_BASE = (
    "The green space in Yanaguana Garden and Civic Park covers thirteen acres in "
    "downtown San Antonio. Donor sponsored trees have already been planted, so "
    "creating a pilot programme this year is necessary. At Civic Park the rows of "
    "Mexican Sycamores that flank the main promenade stand twenty feet apart "
    "across it and eighteen feet along its length. Visitors may scan a code "
    "beside any tree to open a recorded tribute, a photograph and a short message "
    "from the family who gave it. Future phases will add historic structures, "
    "renderings of planned amenities and a searchable directory of every donor."
)
_PADDED_FIGURE = (
    "> **Figure:** Civic Park - 2023 Tower Park - 2025 Yanaguana Garden - 2015. "
    + _PADDED_BASE
)


def test_a_padded_reply_that_did_read_the_picture_is_kept(scanned_pdf, serial):
    novel = len(
        transcribe._content_words(_PADDED_FIGURE)
        - transcribe._content_words(_PADDED_BASE)
    )
    assert transcribe._novelty(_PADDED_FIGURE, _PADDED_BASE) < 0.10, (
        "fixture must reproduce the dilution, else the test is vacuous"
    )
    assert novel >= 3, "fixture must carry real new information"

    vision = FakeVisionClient(reply=_PADDED_BASE, figure_reply=_PADDED_FIGURE)

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "2023" in result.markdown and "Tower Park" in result.markdown
    assert "| figure pass -->" in result.markdown


def test_one_stray_novel_word_does_not_clear_the_floor(scanned_pdf, serial):
    """The page-9 shape: a table of insurance limits reported as figures, whose
    only new token was "employer's". One is not information."""
    vision = FakeVisionClient(
        reply=_PADDED_BASE,
        figure_reply="> **Figure:** " + _PADDED_BASE + " Employer's.",
    )

    result = transcribe_document(scanned_pdf, vision=vision)

    assert "figure pass -->" not in result.markdown


# ── decks: one image per slide, never a text extract ────────────────────────


def _slide_deck(tmp_path, text="Migrate the platform to Kubernetes on AWS."):
    """A .pptx whose fake conversion yields a PDF with a FAT text layer.

    The text layer matters: it is what would send every slide down the free
    text route if the slides kind were not forcing vision. A deck whose
    converted PDF had no text would pass the test for the wrong reason.
    """
    import zipfile

    import pymupdf

    path = tmp_path / "clientdeck.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", "<presentation/>")

    def convert(src, outdir):
        doc = pymupdf.open()
        for n in (1, 2):
            page = doc.new_page(width=720, height=405)
            page.insert_text((40, 60), f"Slide {n}: {text}", fontsize=14)
            page.insert_text((40, 90), text * 4, fontsize=10)
        doc.save(outdir / f"{src.stem}.pdf")
        doc.close()

    return path, convert


def test_every_slide_takes_the_vision_route(tmp_path, monkeypatch):
    """No text extracts. Both slides carry a text layer far over
    text_layer_min_chars and both must still be rasterized."""
    from stratpoint_rag.docparse import slides as slides_mod

    path, convert = _slide_deck(tmp_path)
    monkeypatch.setattr(slides_mod, "_soffice_convert", convert)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.pages_total == 2
    assert result.pages_via_vision == 2


def test_deck_provenance_names_the_original_not_the_derived_pdf(
    tmp_path, monkeypatch
):
    """The visitor uploaded a .pptx. A hash of a PDF they never saw cannot be
    checked against anything they hold."""
    import hashlib

    from stratpoint_rag.docparse import slides as slides_mod

    path, convert = _slide_deck(tmp_path)
    monkeypatch.setattr(slides_mod, "_soffice_convert", convert)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.source_file == "clientdeck.pptx"
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
