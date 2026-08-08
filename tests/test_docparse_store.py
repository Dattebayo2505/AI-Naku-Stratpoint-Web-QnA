"""docparse.store — upload dirs, sha256 cache, TTL sweep, purge.

Session-scoped layout: data/uploads/<session_id>/<upload_id>/. One user must
not be able to reference another's upload_id by guessing, and a session reset
must be able to drop the whole subtree.
"""

from __future__ import annotations

import hashlib

import pytest

from stratpoint_rag.docparse import store


PDF = b"%PDF-1.7\n" + b"x" * 200


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


# ── ids ─────────────────────────────────────────────────────────────────────


def test_new_upload_id_is_opaque_and_unique():
    ids = {store.new_upload_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(store.is_safe_id(i) for i in ids)


@pytest.mark.parametrize(
    "bad",
    ["..", "../etc", "a/b", "a\\b", "", "a" * 200, ".", "a b", "a.b"],
)
def test_unsafe_ids_are_rejected(bad):
    assert store.is_safe_id(bad) is False


@pytest.mark.parametrize("good", ["abc123", "A-B_c", "0", "f" * 64])
def test_safe_ids_are_accepted(good):
    assert store.is_safe_id(good) is True


def test_path_traversal_in_session_id_is_refused(uploads):
    """session_id and upload_id arrive over HTTP; ../../.env must not resolve."""
    with pytest.raises(ValueError):
        store.save_upload("../..", store.new_upload_id(), "b.pdf", PDF)


def test_path_traversal_in_upload_id_is_refused(uploads):
    with pytest.raises(ValueError):
        store.save_upload("sess", "../../etc", "b.pdf", PDF)


# ── saving and locating ─────────────────────────────────────────────────────


def test_save_upload_writes_the_file_and_returns_its_path(uploads):
    uid = store.new_upload_id()

    record = store.save_upload("sess", uid, "client-brief.pdf", PDF)

    assert record.path.read_bytes() == PDF
    assert record.path.parent == uploads / "sess" / uid
    assert record.filename == "client-brief.pdf"


def test_save_upload_records_the_sha256(uploads):
    record = store.save_upload("sess", store.new_upload_id(), "b.pdf", PDF)

    assert record.sha256 == hashlib.sha256(PDF).hexdigest()


def test_saved_upload_can_be_found_again(uploads):
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "b.pdf", PDF)

    found = store.find_upload("sess", uid)

    assert found is not None
    assert found.filename == "b.pdf"
    assert found.path.read_bytes() == PDF


def test_find_upload_returns_none_for_an_unknown_id(uploads):
    assert store.find_upload("sess", store.new_upload_id()) is None


def test_one_session_cannot_read_anothers_upload(uploads):
    uid = store.new_upload_id()
    store.save_upload("alice", uid, "confidential.pdf", PDF)

    assert store.find_upload("bob", uid) is None


def test_a_hostile_filename_is_sanitised(uploads):
    """The filename is user-controlled and ends up as a path component."""
    record = store.save_upload("sess", store.new_upload_id(), "../../../.env", PDF)

    assert record.path.parent.name != ".."
    assert record.path.parent.parent.name == "sess"
    assert ".." not in record.path.name


def test_oversize_upload_is_refused(uploads, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "100")

    with pytest.raises(store.UploadTooLarge):
        store.save_upload("sess", store.new_upload_id(), "big.pdf", PDF)


# ── the sha256 cache ────────────────────────────────────────────────────────


def test_reuploading_the_same_brief_is_found_by_hash(uploads):
    """Mirrors the crawler's content_hash convention — re-parsing is free."""
    store.save_upload("sess", store.new_upload_id(), "b.pdf", PDF)

    hit = store.find_by_sha256("sess", hashlib.sha256(PDF).hexdigest())

    assert hit is not None
    assert hit.filename == "b.pdf"


def test_cache_lookup_misses_for_different_content(uploads):
    store.save_upload("sess", store.new_upload_id(), "b.pdf", PDF)

    assert store.find_by_sha256("sess", hashlib.sha256(b"other").hexdigest()) is None


def test_cache_is_scoped_to_the_session(uploads):
    store.save_upload("alice", store.new_upload_id(), "b.pdf", PDF)

    assert store.find_by_sha256("bob", hashlib.sha256(PDF).hexdigest()) is None


# ── transcription artifact ──────────────────────────────────────────────────


def test_transcription_is_saved_beside_the_upload(uploads):
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "b.pdf", PDF)

    path = store.save_transcription("sess", uid, "---\nsource_file: b.pdf\n---\n\n## Page 1")

    assert path.parent == uploads / "sess" / uid
    assert path.read_text(encoding="utf-8").startswith("---")


def test_saving_a_transcription_for_an_unknown_upload_raises(uploads):
    with pytest.raises(FileNotFoundError):
        store.save_transcription("sess", store.new_upload_id(), "# nope")


def test_provenance_is_stored_structurally_not_reparsed_from_markdown(uploads):
    """The API serves cached parses from this, so it must not depend on
    re-reading YAML frontmatter — the two would drift, and pages_via_vision is
    not in the frontmatter at all."""
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "b.pdf", PDF)

    store.save_transcription(
        "sess", uid, "## Page 1", provenance={"pages_parsed": 11, "pages_failed": [7]}
    )

    record = store.find_upload("sess", uid)
    assert record.provenance == {"pages_parsed": 11, "pages_failed": [7]}


def test_provenance_is_none_before_a_transcription_exists(uploads):
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "b.pdf", PDF)

    assert store.find_upload("sess", uid).provenance is None


def test_saving_a_transcription_preserves_the_upload_metadata(uploads):
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "client-brief.pdf", PDF)

    store.save_transcription("sess", uid, "## Page 1", provenance={"pages_parsed": 1})

    record = store.find_upload("sess", uid)
    assert record.filename == "client-brief.pdf"
    assert record.sha256 == hashlib.sha256(PDF).hexdigest()
    assert record.path.read_bytes() == PDF


# ── deletion ────────────────────────────────────────────────────────────────


def test_delete_upload_removes_the_directory(uploads):
    uid = store.new_upload_id()
    store.save_upload("sess", uid, "b.pdf", PDF)

    assert store.delete_upload("sess", uid) is True
    assert store.find_upload("sess", uid) is None
    assert not (uploads / "sess" / uid).exists()


def test_delete_upload_is_idempotent(uploads):
    assert store.delete_upload("sess", store.new_upload_id()) is False


def test_delete_session_drops_the_whole_subtree(uploads):
    """'Reset conversation' must not leave confidential briefs on disk."""
    for _ in range(3):
        store.save_upload("sess", store.new_upload_id(), "b.pdf", PDF)

    store.delete_session("sess")

    assert not (uploads / "sess").exists()


def test_purge_all_wipes_every_session(uploads):
    store.save_upload("alice", store.new_upload_id(), "b.pdf", PDF)
    store.save_upload("bob", store.new_upload_id(), "b.pdf", PDF)

    store.purge_all()

    assert not any(uploads.iterdir()) if uploads.exists() else True


def test_purge_all_is_safe_when_nothing_exists(uploads):
    store.purge_all()  # must not raise


# ── the TTL sweep ───────────────────────────────────────────────────────────
#
# No clock inside the module — `now` is injected, exactly as the crawler stamps
# crawled_at once in cli._run and threads it through.


def test_sweep_deletes_directories_older_than_the_ttl(uploads, monkeypatch):
    monkeypatch.setenv("UPLOAD_TTL_SECONDS", "3600")
    uid = store.new_upload_id()
    record = store.save_upload("sess", uid, "b.pdf", PDF)
    mtime = record.path.parent.stat().st_mtime

    removed = store.sweep(now=mtime + 3601)

    assert removed == 1
    assert store.find_upload("sess", uid) is None


def test_sweep_keeps_fresh_directories(uploads, monkeypatch):
    monkeypatch.setenv("UPLOAD_TTL_SECONDS", "3600")
    uid = store.new_upload_id()
    record = store.save_upload("sess", uid, "b.pdf", PDF)
    mtime = record.path.parent.stat().st_mtime

    removed = store.sweep(now=mtime + 60)

    assert removed == 0
    assert store.find_upload("sess", uid) is not None


def test_sweep_spans_every_session(uploads, monkeypatch):
    monkeypatch.setenv("UPLOAD_TTL_SECONDS", "10")
    a = store.save_upload("alice", store.new_upload_id(), "b.pdf", PDF)
    store.save_upload("bob", store.new_upload_id(), "b.pdf", PDF)

    removed = store.sweep(now=a.path.parent.stat().st_mtime + 999)

    assert removed == 2


def test_sweep_removes_the_session_dir_once_it_is_empty(uploads, monkeypatch):
    monkeypatch.setenv("UPLOAD_TTL_SECONDS", "10")
    record = store.save_upload("sess", store.new_upload_id(), "b.pdf", PDF)

    store.sweep(now=record.path.parent.stat().st_mtime + 999)

    assert not (uploads / "sess").exists()


def test_sweep_is_safe_when_the_root_does_not_exist(uploads):
    assert store.sweep(now=0.0) == 0
