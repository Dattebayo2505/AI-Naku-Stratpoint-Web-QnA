from pathlib import Path

from stratpoint_crawler.config import Settings
from stratpoint_crawler.extract import extract

FIX = Path(__file__).parent / "fixtures"


def test_extract_strips_chrome_and_keeps_body():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/about/", settings=Settings())
    assert content.title == "About Stratpoint"
    assert "About Stratpoint" in content.markdown
    assert "Home Services Careers" not in content.markdown
    assert "Copyright 2026" not in content.markdown
    assert "tracking" not in content.markdown


def test_extract_preserves_inline_links():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/about/", settings=Settings())
    assert "(https://stratpoint.com/services/)" in content.markdown


def test_extract_hash_is_stable_and_prefixed():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    s = Settings()
    a = extract(html, url="https://stratpoint.com/about/", settings=s)
    b = extract(html, url="https://stratpoint.com/about/", settings=s)
    assert a.content_hash == b.content_hash
    assert a.content_hash.startswith("sha256:")


def test_extract_flags_thin_content():
    html = (FIX / "thin.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/empty/", settings=Settings())
    assert content.thin is True


def test_extract_divi_layout_drops_related_posts():
    """Real Divi pages have no <main>, ~10 small <article> related-post cards, and
    body text inside .et_pb_text_inner divs. The extractor must keep the case-study
    text and drop the related-posts widget."""
    html = (FIX / "divi.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/case/", settings=Settings())
    assert "Solaire Resort and Casino" in content.markdown
    assert "monitoring stack" in content.markdown
    assert "Related Post" not in content.markdown
    assert "Top nav" not in content.markdown
    assert "Copyright Stratpoint" not in content.markdown
