"""Attachment bookkeeping for the sidebar.

Pure functions over a plain list so the rerun-safety logic is testable without
a Streamlit script context. ui/app.py holds the widgets; the rules live here.
"""

from __future__ import annotations

import pytest

from stratpoint_rag.ui import attachments as att


def _rec(upload_id="a3f9c2", sha256="deadbeef", filename="client-brief.pdf", **kw):
    return {"upload_id": upload_id, "sha256": sha256, "filename": filename,
            "pages": 12, **kw}


# ── rerun safety ────────────────────────────────────────────────────────────
#
# st.file_uploader returns the SAME file on every rerun, and Streamlit
# re-executes app.py top-to-bottom on every widget interaction, including each
# chat message. Naive code re-POSTs /upload on every submit.


def test_a_file_already_uploaded_is_recognised_by_hash():
    current = [_rec(sha256="abc123")]

    assert att.find_by_hash(current, "abc123") is not None


def test_an_unseen_file_is_not_recognised():
    assert att.find_by_hash([_rec(sha256="abc123")], "other") is None


def test_find_by_hash_on_an_empty_list_is_none():
    assert att.find_by_hash([], "abc123") is None


def test_adding_the_same_upload_twice_does_not_duplicate_it():
    current = att.add([], _rec())

    current = att.add(current, _rec())

    assert len(current) == 1


def test_add_returns_a_new_list_rather_than_mutating():
    original = []

    result = att.add(original, _rec())

    assert original == []
    assert len(result) == 1


def test_remove_drops_only_the_named_upload():
    current = [_rec(upload_id="one"), _rec(upload_id="two", sha256="x")]

    result = att.remove(current, "one")

    assert [a["upload_id"] for a in result] == ["two"]


def test_removing_an_unknown_upload_is_a_no_op():
    current = [_rec(upload_id="one")]

    assert att.remove(current, "nope") == current


# ── the provenance chip ─────────────────────────────────────────────────────
#
# Surfacing pages_parsed/pages_failed is what makes them worth having: it tells
# the user their scanned page 7 didn't make it BEFORE they act on a quote.


def test_chip_shows_filename_and_page_count():
    label = att.chip_label(_rec(pages=12))

    assert "client-brief.pdf" in label
    assert "12 pages" in label


def test_chip_reports_vision_pages():
    label = att.chip_label(_rec(pages=12, pages_via_vision=2))

    assert "2 via vision" in label


def test_chip_reports_failed_pages():
    label = att.chip_label(_rec(pages=12, pages_failed=[7]))

    assert "1 failed" in label


def test_chip_pluralises_failures():
    label = att.chip_label(_rec(pages=20, pages_failed=[3, 7, 9]))

    assert "3 failed" in label


def test_chip_omits_zero_counts():
    """A clean parse should not read like a report card of zeroes."""
    label = att.chip_label(_rec(pages=12, pages_via_vision=0, pages_failed=[]))

    assert "via vision" not in label
    assert "failed" not in label


def test_chip_handles_a_single_page():
    assert "1 page" in att.chip_label(_rec(pages=1))


def test_chip_marks_a_not_yet_parsed_upload():
    label = att.chip_label(_rec(pages=12, parsed=False))

    assert "not transcribed" in label.lower()


def test_chip_flags_truncation():
    """A 30-page brief capped at 20 must not look like a complete parse."""
    label = att.chip_label(_rec(pages=30, pages_parsed=20, truncated=True))

    assert "first 20" in label


# ── the confirmation dialog's estimate ──────────────────────────────────────


@pytest.mark.parametrize(
    "pages, floor, ceiling",
    [(1, 1, 20), (12, 20, 200), (20, 30, 300)],
)
def test_transcription_estimate_scales_with_pages(pages, floor, ceiling):
    seconds = att.estimate_seconds(pages)

    assert floor <= seconds <= ceiling


def test_estimate_is_monotonic():
    assert att.estimate_seconds(20) > att.estimate_seconds(5)
