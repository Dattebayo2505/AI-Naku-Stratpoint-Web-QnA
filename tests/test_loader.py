from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from stratpoint_rag.rag.ingest import ingest
from stratpoint_rag.rag.loader import (
    MIN_PAGE_BODY_CHARS,
    Page,
    load_manifest,
    load_pages,
    strip_frontmatter,
)
from stratpoint_rag.rag.models import Chunk


def test_strip_frontmatter():
    text_with_fm = "---\nurl: https://stratpoint.com/about\ntitle: About\n---\n# About Stratpoint\n\nContent here."
    assert strip_frontmatter(text_with_fm) == "# About Stratpoint\n\nContent here."

    text_no_fm = "# Direct Markdown\n\nNo frontmatter at all."
    assert strip_frontmatter(text_no_fm) == text_no_fm

    text_incomplete_fm = "---\nurl: broken without closing"
    assert strip_frontmatter(text_incomplete_fm) == text_incomplete_fm


def test_load_manifest_filters_present_statuses(tmp_path: Path):
    index_file = tmp_path / "index.jsonl"
    rows = [
        {"url": "https://stratpoint.com/ok", "slug": "ok-page", "title": "OK", "status": "ok", "content_hash": "h1"},
        {"url": "https://stratpoint.com/skipped", "slug": "skipped-page", "title": "Skipped", "status": "skipped", "content_hash": "h2"},
        {"url": "https://stratpoint.com/failed", "slug": "failed-page", "title": "Failed", "status": "failed", "content_hash": None},
        {"url": "https://stratpoint.com/unknown", "slug": "unknown-page", "title": "Unknown", "status": "other", "content_hash": None},
    ]
    index_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")

    manifest = load_manifest(index_file)
    slugs = [r["slug"] for r in manifest]
    assert slugs == ["ok-page", "skipped-page"]


def test_load_pages_skips_stub_pages_under_100_chars(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    index_file = tmp_path / "index.jsonl"

    rows = [
        {"url": "https://stratpoint.com/stub1", "slug": "stub-empty", "title": "Empty Stub", "status": "ok", "content_hash": "h1"},
        {"url": "https://stratpoint.com/stub2", "slug": "stub-icon", "title": "Icon Stub", "status": "ok", "content_hash": "h2"},
        {"url": "https://stratpoint.com/valid", "slug": "valid-page", "title": "Valid Page", "status": "ok", "content_hash": "h3"},
    ]
    index_file.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    # Empty stub
    (pages_dir / "stub-empty.md").write_text(
        "---\nurl: https://stratpoint.com/stub1\n---\n   \n\n", encoding="utf-8"
    )
    # 8-char icon stub like project__qa-testing-only-projects-v2-1.md
    (pages_dir / "stub-icon.md").write_text(
        "---\nurl: https://stratpoint.com/stub2\n---\n╳\n\n![]()\n", encoding="utf-8"
    )
    # Valid substantive page >= 100 chars body
    valid_body = "Stratpoint provides enterprise-grade digital transformation, custom software engineering, and AI-driven solutions across industries worldwide."
    assert len(valid_body) >= 100
    (pages_dir / "valid-page.md").write_text(
        f"---\nurl: https://stratpoint.com/valid\n---\n{valid_body}\n", encoding="utf-8"
    )

    with caplog.at_level(logging.INFO):
        pages = load_pages(tmp_path)

    assert len(pages) == 1
    assert pages[0].slug == "valid-page"
    assert pages[0].body.strip() == valid_body

    # Check logs for skipped stub pages
    assert "skipping stub-empty: thin/stub page" in caplog.text
    assert "skipping stub-icon: thin/stub page" in caplog.text


def test_load_pages_custom_min_body_chars(tmp_path: Path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    index_file = tmp_path / "index.jsonl"

    row = {"url": "https://stratpoint.com/short", "slug": "short-page", "title": "Short", "status": "ok", "content_hash": "h1"}
    index_file.write_text(json.dumps(row) + "\n", encoding="utf-8")

    short_body = "This is a 45 characters long body text sentence."
    (pages_dir / "short-page.md").write_text(f"---\nurl: ...\n---\n{short_body}\n", encoding="utf-8")

    # Default MIN_PAGE_BODY_CHARS (100) skips it
    assert len(load_pages(tmp_path)) == 0

    # Custom min_body_chars=30 loads it
    loaded_with_low_min = load_pages(tmp_path, min_body_chars=30)
    assert len(loaded_with_low_min) == 1
    assert loaded_with_low_min[0].slug == "short-page"

    # Custom min_body_chars=60 skips it
    assert len(load_pages(tmp_path, min_body_chars=60)) == 0


def test_load_pages_missing_file_logs_warning_and_skips(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    index_file = tmp_path / "index.jsonl"
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)

    rows = [
        {"url": "https://stratpoint.com/missing", "slug": "missing-page", "title": "Missing", "status": "ok", "content_hash": "h1"},
        {"url": "https://stratpoint.com/valid", "slug": "valid-page", "title": "Valid", "status": "ok", "content_hash": "h2"},
    ]
    index_file.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    valid_body = "A" * 120
    (pages_dir / "valid-page.md").write_text(f"---\nurl: ...\n---\n{valid_body}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        pages = load_pages(tmp_path)

    assert len(pages) == 1
    assert pages[0].slug == "valid-page"
    assert "skipping missing-page: page file missing" in caplog.text


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class _FakeStore:
    def __init__(self, initial_slugs: dict[str, str] | None = None):
        # slug -> content_hash
        self._stored: dict[str, str] = initial_slugs or {}
        self.upserted_slugs: list[str] = []
        self.deleted_slugs: list[str] = []

    def stored_hash(self, slug: str) -> str | None:
        return self._stored.get(slug)

    def slugs(self) -> set[str]:
        return set(self._stored.keys())

    def delete_slug(self, slug: str) -> None:
        self.deleted_slugs.append(slug)
        self._stored.pop(slug, None)

    def upsert_page(self, chunks: list[Chunk], embeddings: list[list[float]], content_hash: str) -> None:
        slug = chunks[0].slug
        self.upserted_slugs.append(slug)
        self._stored[slug] = content_hash


def test_ingest_filters_stubs_and_removes_prior_stub_embeddings(tmp_path: Path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    index_file = tmp_path / "index.jsonl"

    rows = [
        {"url": "https://stratpoint.com/valid", "slug": "valid-page", "title": "Valid", "status": "ok", "content_hash": "h1"},
        {"url": "https://stratpoint.com/stub", "slug": "stub-page", "title": "Stub", "status": "ok", "content_hash": "h2"},
        {"url": "https://stratpoint.com/missing", "slug": "missing-page", "title": "Missing", "status": "ok", "content_hash": "h3"},
    ]
    index_file.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    (pages_dir / "valid-page.md").write_text(f"---\nurl: ...\n---\n{'V' * 150}", encoding="utf-8")
    (pages_dir / "stub-page.md").write_text("---\nurl: ...\n---\n╳ ![]()", encoding="utf-8")
    # missing-page.md is intentionally not written to disk

    # Pre-populate store with:
    # - valid-page (will be skipped since hash matches)
    # - stub-page (was in store from an older crawl/ingest, must be removed)
    # - missing-page (transiently missing, must NOT be removed)
    # - dropped-page (not in manifest at all, must be removed)
    fake_store = _FakeStore({
        "valid-page": "h1",
        "stub-page": "h2",
        "missing-page": "h3",
        "dropped-page": "h4",
    })

    stats = ingest(str(tmp_path), embedder=_FakeEmbedder(), store=fake_store)

    assert stats["added"] == 0
    assert stats["skipped"] == 1  # valid-page had unchanged hash
    assert stats["removed"] == 2  # stub-page and dropped-page were evicted

    assert "stub-page" in fake_store.deleted_slugs
    assert "dropped-page" in fake_store.deleted_slugs
    assert "missing-page" not in fake_store.deleted_slugs
    assert fake_store.slugs() == {"valid-page", "missing-page"}
