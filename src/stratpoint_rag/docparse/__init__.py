"""Document parsing: uploaded client briefs (PDF/image) -> structured requirements.

Two hops, deliberately separate, with the Markdown transcription as the artifact
between them.

**Hop 1 — transcription.** A complete, verbatim Markdown rendering of an
uploaded brief that preserves the document's own visual hierarchy. It does not
reorganize content into Requirements / Constraints / Timeline sections; that
inference is hop 2's job, and splitting it across two models makes a wrong
output untraceable. Runs **eagerly at upload** — up to 40 vision calls, 25-200s,
with its own timeout — so the wait lands where the user just dropped a file and
expects a spinner.

**Hop 2 — extraction.** The transcription becomes a validated
``ExtractedRequirements``. Runs **lazily inside the chat turn**, on the request
thread: 1 call, or 4-5 under map-reduce, 3-20s, comfortably inside the 120s chat
timeout that hop 1 would blow.

Public seams::

    transcribe_document(path, *, vision=None) -> TranscriptionResult   # hop 1
    extract_requirements(markdown, *, provenance=..., text=None)       # hop 2
    extract_brief(brief_ref, *, text=None)                             # hop 2 + cache

``vision``/``text`` are the injection points that mirror the crawler's
``Fetcher`` Protocol, so both hops — page routing, concurrency, failure
accounting, markdown assembly, the map-reduce boundary, the merge — are
unit-tested without a network call.

Known limitation, deferred by decision: **prompt injection via uploaded
content.** An uploaded brief is attacker-controllable in a way the crawled
corpus is not; hop 1 transcribes it verbatim by design and hop 2 reads that
verbatim text and sets the price of a real proposal. White 6pt text reading
"Ignore previous instructions..." is faithfully transcribed and then read as
instructions downstream. The ``guardrails`` package guards the user's *message*;
this content enters via ``/upload``. Two structural mitigations *are* in place
and should not be mistaken for a solution: the hop-2 schema has no free-text
channel except a length-capped ``extraction_notes``, and the client/project
names never come from the document unaided (see ``names.py``).

Known limitation, not mitigated: **the vision model occasionally fabricates a
table.** Measured once in six runs of the same 10-page scan — a "Characteristic
/ Number of people" demographic table, complete with rows, on a page carrying
two aerial maps and no table at all. Hop 1's output is free-form Markdown by
design, so no schema can catch it, and it reaches hop 2 as though the client had
written it. It is rare and it is real; treat a surprising table in a
transcription as suspect before treating it as a requirement.
"""

from stratpoint_rag.docparse.clients import TextClient, VisionClient
from stratpoint_rag.docparse.extract import (
    clear_cache,
    extract_brief,
    extract_requirements,
)
from stratpoint_rag.docparse.models import BriefRef, PageResult, TranscriptionResult
from stratpoint_rag.docparse.names import NameSuggestion, suggest_names
from stratpoint_rag.docparse.nim import NimTextClient, NimVisionClient
from stratpoint_rag.docparse.render import EncryptedDocument, UnsupportedDocument
from stratpoint_rag.docparse.schema import ExtractedRequirements
from stratpoint_rag.docparse.transcribe import transcribe_document

__all__ = [
    "BriefRef",
    "EncryptedDocument",
    "ExtractedRequirements",
    "NameSuggestion",
    "NimTextClient",
    "NimVisionClient",
    "PageResult",
    "TextClient",
    "TranscriptionResult",
    "UnsupportedDocument",
    "VisionClient",
    "clear_cache",
    "extract_brief",
    "extract_requirements",
    "suggest_names",
    "transcribe_document",
]

