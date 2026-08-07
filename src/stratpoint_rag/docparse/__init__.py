"""Document parsing: uploaded client briefs (PDF/image) -> structured Markdown.

Hop 1 (this package's current scope) is **transcription only** — a complete,
verbatim Markdown rendering of an uploaded brief that preserves the document's
own visual hierarchy. It deliberately does not reorganize content into
Requirements / Constraints / Timeline sections; that inference is hop 2's job,
and splitting it across two models makes a wrong output untraceable.

Public seam::

    transcribe_document(path, *, vision=None) -> TranscriptionResult

``vision`` is a ``VisionClient`` — the injection point that mirrors the
crawler's ``Fetcher`` Protocol, so the whole page loop (routing, concurrency,
failure accounting, markdown assembly) is unit-tested without a network call.

Known limitation, deferred by decision: **prompt injection via uploaded
content.** An uploaded brief is attacker-controllable in a way the crawled
corpus is not, and hop 1 transcribes it verbatim by design. White 6pt text
reading "Ignore previous instructions..." is faithfully transcribed and then
read as instructions downstream. The ``guardrails`` package guards the user's
*message*; this content enters via ``/upload``.
"""

from stratpoint_rag.docparse.clients import TextClient, VisionClient
from stratpoint_rag.docparse.models import PageResult, TranscriptionResult
from stratpoint_rag.docparse.nim import NimVisionClient
from stratpoint_rag.docparse.render import EncryptedDocument, UnsupportedDocument
from stratpoint_rag.docparse.transcribe import transcribe_document

__all__ = [
    "EncryptedDocument",
    "NimVisionClient",
    "PageResult",
    "TextClient",
    "TranscriptionResult",
    "UnsupportedDocument",
    "VisionClient",
    "transcribe_document",
]

