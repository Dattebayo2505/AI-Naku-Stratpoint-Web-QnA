"""Model-call seams for docparse.

Mirrors the crawler's ``Fetcher`` Protocol (``stratpoint_crawl/crawler.py:15``).
CLAUDE.md on why that shape is load-bearing: *"this is why the entire crawl
loop is unit-tested without a browser. Keep it that way."* Same rule here —
``transcribe.py`` must never import or construct an HTTP client directly.

Both methods return ``(text, usage)``. Usage is **returned, never accumulated
here**: page work runs on a ``ThreadPoolExecutor``, and ``llmops/usage.py`` is a
``threading.local()`` whose docstring assumes one request per thread. A worker
calling ``add_usage()`` writes to an accumulator the request thread never
reads. The caller aggregates — the same shape as the crawler, where workers
return results and the caller accounts for them.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["TextClient", "VisionClient"]


class VisionClient(Protocol):
    """Transcribes one page image. One image per request — the endpoint
    hard-refuses two with HTTP 400 before inference."""

    def describe(self, image_jpeg: bytes, prompt: str) -> tuple[str, dict]: ...


class TextClient(Protocol):
    """Text-only completion. Unused in hop 1; defined now so hop 2's structured
    extraction has the same offline-testable seam from the start."""

    def complete(self, system: str, user: str) -> tuple[str, dict]: ...
