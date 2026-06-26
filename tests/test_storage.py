import json

from stratpoint_crawler.models import PageContent, PageResult
from stratpoint_crawler.storage import slugify, Writer


def test_slugify_paths():
    assert slugify("https://stratpoint.com/") == "index"
    assert slugify("https://stratpoint.com/about/") == "about"
    assert slugify("https://stratpoint.com/insights/blog/foo/") == "insights__blog__foo"


def test_writer_emits_markdown_with_frontmatter(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/about/", title="About",
                          markdown="# About\n\nBody.", text_len=14,
                          content_hash="sha256:abc")
    w.write_page("about", content, raw_html="<html></html>")

    md_file = tmp_path / "pages" / "about.md"
    text = md_file.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "url: https://stratpoint.com/about/" in text
    assert "content_hash: sha256:abc" in text
    assert "# About" in text
    assert not (tmp_path / "raw_html").exists()


def test_writer_save_html_toggle(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z", save_html=True)
    content = PageContent(url="https://stratpoint.com/x/", title="X",
                          markdown="x", text_len=1, content_hash="sha256:x")
    w.write_page("x", content, raw_html="<html>raw</html>")
    assert (tmp_path / "raw_html" / "x.html").read_text(encoding="utf-8") == "<html>raw</html>"


def test_writer_index_persists_lastmod_and_status(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/about/", title="About",
                          markdown="b", text_len=1, content_hash="sha256:abc")
    results = [
        PageResult(url="https://stratpoint.com/about/", slug="about",
                   status="ok", content=content, lastmod="2025-05-20"),
        PageResult(url="https://stratpoint.com/dead/", slug="dead",
                   status="failed", error="timeout"),
    ]
    w.write_index(results)

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["lastmod"] == "2025-05-20"      # no longer hardcoded None
    assert first["status"] == "ok"
    second = json.loads(lines[1])
    assert second["status"] == "failed" and second["content_hash"] is None


def test_writer_index_carries_skipped_records(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/new/", title="New",
                          markdown="n", text_len=1, content_hash="sha256:new")
    fresh = [PageResult(url="https://stratpoint.com/new/", slug="new",
                        status="ok", content=content, lastmod="2026-06-01")]
    carried = [{
        "url": "https://stratpoint.com/about/", "title": "About", "slug": "about",
        "lastmod": "2025-05-20", "crawled_at": "2026-06-10T00:00:00Z",
        "content_hash": "sha256:abc", "text_len": 42, "status": "ok", "error": None,
    }]
    w.write_index(fresh, carried_records=carried)

    rows = [json.loads(l) for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    by_url = {r["url"]: r for r in rows}
    assert by_url["https://stratpoint.com/new/"]["status"] == "ok"
    carried_row = by_url["https://stratpoint.com/about/"]
    assert carried_row["status"] == "skipped"            # forced
    assert carried_row["content_hash"] == "sha256:abc"   # preserved
    assert carried_row["crawled_at"] == "2026-06-10T00:00:00Z"  # original kept
    assert carried_row["lastmod"] == "2025-05-20"
