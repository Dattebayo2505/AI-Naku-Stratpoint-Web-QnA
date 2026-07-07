"""Unit tests for the UI resource-fetch safety logic (offline; no Streamlit)."""
from stratpoint_rag.ui import resource_fetch as rf


def _gai(ip):
    """Fake socket.getaddrinfo returning a single resolved IP."""
    return lambda host, *a, **k: [(None, None, None, "", (ip, 0))]


def test_mime_for_known_and_unknown():
    assert rf.mime_for("https://x.com/a/report.pdf") == "application/pdf"
    assert rf.mime_for("https://x.com/a/deck.pptx").endswith("presentationml.presentation")
    assert rf.mime_for("https://x.com/a/paper.docx").endswith("wordprocessingml.document")
    assert rf.mime_for("https://x.com/a/file.xyz") == "application/octet-stream"


def test_filename_for():
    assert rf.filename_for("https://x.com/docs/My%20Report.pdf") == "My Report.pdf"
    assert rf.filename_for("https://x.com/") == "download"


def test_is_public_http_rejects_non_http_schemes():
    assert rf._is_public_http("ftp://example.com/x.pdf") is False
    assert rf._is_public_http("file:///etc/passwd") is False
    assert rf._is_public_http("not a url") is False


def test_is_public_http_rejects_loopback(monkeypatch):
    monkeypatch.setattr(rf.socket, "getaddrinfo", _gai("127.0.0.1"))
    assert rf._is_public_http("http://localhost/x.pdf") is False


def test_is_public_http_rejects_private_range(monkeypatch):
    monkeypatch.setattr(rf.socket, "getaddrinfo", _gai("10.0.0.5"))
    assert rf._is_public_http("http://internal.host/x.pdf") is False


def test_is_public_http_rejects_link_local_metadata(monkeypatch):
    # The classic SSRF target (cloud metadata endpoint).
    monkeypatch.setattr(rf.socket, "getaddrinfo", _gai("169.254.169.254"))
    assert rf._is_public_http("http://metadata/x.pdf") is False


def test_is_public_http_allows_public(monkeypatch):
    monkeypatch.setattr(rf.socket, "getaddrinfo", _gai("93.184.216.34"))
    assert rf._is_public_http("https://example.com/x.pdf") is True


def test_safe_fetch_blocks_non_public(monkeypatch):
    monkeypatch.setattr(rf, "_is_public_http", lambda u: False)
    assert rf.safe_fetch("http://localhost/x.pdf") is None


class _FakeResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        yield from self._chunks


def test_safe_fetch_returns_bytes(monkeypatch):
    monkeypatch.setattr(rf, "_is_public_http", lambda u: True)
    monkeypatch.setattr(rf.requests, "get", lambda *a, **k: _FakeResp([b"PDF", b"DATA"]))
    assert rf.safe_fetch("https://example.com/x.pdf") == b"PDFDATA"


def test_safe_fetch_enforces_size_cap(monkeypatch):
    monkeypatch.setattr(rf, "_is_public_http", lambda u: True)
    monkeypatch.setattr(rf.requests, "get", lambda *a, **k: _FakeResp([b"x" * 10]))
    assert rf.safe_fetch("https://example.com/x.pdf", max_bytes=5) is None


def test_safe_fetch_swallows_request_error(monkeypatch):
    monkeypatch.setattr(rf, "_is_public_http", lambda u: True)

    def boom(*a, **k):
        raise rf.requests.RequestException("nope")

    monkeypatch.setattr(rf.requests, "get", boom)
    assert rf.safe_fetch("https://example.com/x.pdf") is None
