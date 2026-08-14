"""Upload storage: session-scoped dirs, sha256 cache, TTL sweep, purge.

Layout::

    <UPLOAD_DIR>/<session_id>/<upload_id>/
        meta.json          filename + sha256
        <sanitised name>   the uploaded bytes
        converted.pdf      decks only: the LibreOffice-derived PDF (slides.py)
        transcription.md   the hop-1 artifact

Session scoping is a boundary, not tidiness: one user must not reach another's
``upload_id`` by guessing it, and "Reset conversation" has to be able to drop a
whole subtree of confidential briefs in one call.

Three independent cleanup mechanisms, because "delete on next run" is not one
of them. Streamlit re-executes ``ui/app.py`` on every widget interaction, so
keying cleanup to script execution deletes the file the user just uploaded —
and the files live in the API process, a long-lived uvicorn that may not
restart for days.

1. :func:`purge_all` from a FastAPI startup hook — the real "delete on next
   run", keyed to the process that actually owns the files. Handles crashes and
   interrupted sessions, which Streamlit gives no tab-close callback for.
2. :func:`sweep` on each upload — no scheduler, no background thread, and it
   bounds disk on an LXC that never reboots.
3. :func:`delete_upload` / :func:`delete_session`, wired to the UI.

**No clock in this module.** ``sweep`` takes ``now`` from its caller, the same
rule that keeps the crawler's ``storage``/``state`` deterministic under test.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from stratpoint_rag.docparse import config
from stratpoint_rag.docparse.models import BriefRef

__all__ = [
    "UploadRecord",
    "UploadTooLarge",
    "delete_session",
    "delete_upload",
    "find_by_sha256",
    "find_upload",
    "is_safe_id",
    "new_upload_id",
    "purge_all",
    "resolve_briefs",
    "save_transcription",
    "save_upload",
    "sweep",
]

_META = "meta.json"
_TRANSCRIPTION = "transcription.md"

# Names the pipeline owns inside an upload directory. An upload may not take
# one — see _safe_filename. Compared casefolded because the store runs on
# Windows too, where "Meta.JSON" is the same file.
#
# converted.pdf joined the set when decks landed. A .pptx uploaded under that
# name would be its own conversion cache: ensure_pdf's "cached and non-empty"
# check would hand the raw zip back as the derived PDF, and open_document would
# reject a perfectly good deck as an unsupported file. Not corrupting, but
# wrong, and cheaper to exclude here than to special-case in slides.py.
_RESERVED_NAMES = frozenset(
    {_META.casefold(), _TRANSCRIPTION.casefold(), "converted.pdf"}
)

# Ids are ours (uuid4 hex) or the caller's session id, but both arrive back
# over HTTP before being joined onto a path. Allowlist, never blocklist.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class UploadTooLarge(Exception):
    """The payload exceeds UPLOAD_MAX_BYTES."""


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    filename: str
    sha256: str
    path: Path
    # Hop-1 provenance, present once a transcription has been saved. Stored
    # structurally rather than re-derived from the markdown's frontmatter: the
    # two would drift, and pages_via_vision is not in the frontmatter at all.
    provenance: dict | None = None

    @property
    def transcription_path(self) -> Path:
        return self.path.parent / _TRANSCRIPTION


def new_upload_id() -> str:
    return uuid.uuid4().hex


def is_safe_id(value: str) -> bool:
    """True when ``value`` is safe to use as a single path component."""
    return bool(_SAFE_ID.fullmatch(value or ""))


def _root() -> Path:
    return Path(config.upload_dir())


def _dir_for(session_id: str, upload_id: str) -> Path:
    for part, label in ((session_id, "session_id"), (upload_id, "upload_id")):
        if not is_safe_id(part):
            raise ValueError(f"unsafe {label}: {part!r}")
    return _root() / session_id / upload_id


def _safe_filename(name: str) -> str:
    """Reduce a user-supplied filename to one harmless path component.

    Traversal is handled by ``Path(name).name`` plus the character allowlist.
    The reserved-name check is the other half: this directory also holds
    ``meta.json`` and ``transcription.md``, and an upload landing on either of
    those names collides with a file the pipeline owns.

    ``transcription.md`` was the damaging one. The upload was written to the
    exact path hop 1 writes to, so ``resolve_briefs`` saw the artifact on disk
    and marked the brief transcribed without hop 1 ever running — ``read_brief``
    then served the raw upload as though it were a transcription, with all-zero
    page provenance, and ``extract_brief``'s "hop 1 has not run" guard was
    bypassed by construction. ``meta.json`` was merely broken: the metadata
    write two lines later overwrote the upload.

    **Trailing dots and spaces are stripped, not only leading ones.** Win32
    drops them on create, so ``"transcription.md."`` passed the reserved-name
    comparison unchanged and then landed on disk as ``transcription.md`` —
    re-opening the whole bypass above. The same casefolding comment applies:
    this store runs on Windows too.
    """
    stem = Path(name).name  # drops any directory part, including ../
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", stem).strip(". ")
    cleaned = cleaned[:120].strip(". ") or "upload"
    if cleaned.casefold() in _RESERVED_NAMES:
        cleaned = f"upload_{cleaned}"
    return cleaned


def save_upload(
    session_id: str, upload_id: str, filename: str, data: bytes
) -> UploadRecord:
    """Write an uploaded file into its own directory and record its hash."""
    max_bytes = config.upload_max_bytes()
    if len(data) > max_bytes:
        raise UploadTooLarge(
            f"{len(data)} bytes exceeds the {max_bytes}-byte upload limit"
        )

    directory = _dir_for(session_id, upload_id)
    directory.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(filename)
    path = directory / safe_name
    path.write_bytes(data)

    sha256 = hashlib.sha256(data).hexdigest()
    (directory / _META).write_text(
        json.dumps(
            {"upload_id": upload_id, "filename": filename,
             "stored_name": safe_name, "sha256": sha256}
        ),
        encoding="utf-8",
    )
    return UploadRecord(upload_id, filename, sha256, path)


def _read_record(directory: Path) -> UploadRecord | None:
    meta_path = directory / _META
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return UploadRecord(
        upload_id=meta["upload_id"],
        filename=meta["filename"],
        sha256=meta["sha256"],
        path=directory / meta["stored_name"],
        provenance=meta.get("provenance"),
    )


def find_upload(session_id: str, upload_id: str) -> UploadRecord | None:
    """Look up one upload, or None. Session-scoped by construction."""
    try:
        directory = _dir_for(session_id, upload_id)
    except ValueError:
        return None
    return _read_record(directory)


def find_by_sha256(session_id: str, sha256: str) -> UploadRecord | None:
    """The re-upload cache: identical bytes in the same session parse once."""
    if not is_safe_id(session_id):
        return None
    session_dir = _root() / session_id
    if not session_dir.is_dir():
        return None
    for directory in sorted(session_dir.iterdir()):
        record = _read_record(directory) if directory.is_dir() else None
        if record and record.sha256 == sha256:
            return record
    return None


def resolve_briefs(session_id: str, upload_ids: list[str] | None) -> list[BriefRef]:
    """Turn the chat request's opaque upload ids into resolved briefs.

    Unknown ids are dropped rather than raising: an id can go stale between the
    UI's sidebar and the API (TTL sweep, a delete from another tab, a restarted
    uvicorn that purged on boot), and the right response to "that attachment is
    gone" is a conversation without it, not a 500 mid-turn.

    Session-scoped by construction, via ``find_upload``.
    """
    briefs: list[BriefRef] = []
    for upload_id in upload_ids or []:
        record = find_upload(session_id, upload_id)
        if record is None:
            continue
        provenance = record.provenance or {}
        has_markdown = record.transcription_path.is_file()
        briefs.append(
            BriefRef(
                upload_id=record.upload_id,
                filename=record.filename,
                sha256=record.sha256,
                markdown_path=str(record.transcription_path) if has_markdown else None,
                pages_total=int(provenance.get("pages_total") or 0),
                pages_parsed=int(provenance.get("pages_parsed") or 0),
                pages_failed=list(provenance.get("pages_failed") or []),
            )
        )
    return briefs


def save_transcription(
    session_id: str, upload_id: str, markdown: str, *, provenance: dict | None = None
) -> Path:
    """Write the hop-1 artifact beside its source file.

    ``provenance`` is merged into the upload's metadata so a cached parse can be
    served without re-reading the markdown.
    """
    directory = _dir_for(session_id, upload_id)
    meta_path = directory / _META
    if not meta_path.is_file():
        raise FileNotFoundError(f"no upload {upload_id} in session {session_id}")

    path = directory / _TRANSCRIPTION
    path.write_text(markdown, encoding="utf-8")

    if provenance is not None:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["provenance"] = provenance
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def delete_upload(session_id: str, upload_id: str) -> bool:
    """Remove one upload directory. True when something was removed."""
    try:
        directory = _dir_for(session_id, upload_id)
    except ValueError:
        return False
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    _prune_session_dir(directory.parent)
    return not directory.exists()


def delete_session(session_id: str) -> bool:
    """Drop a whole session subtree — wired to 'Reset conversation'."""
    if not is_safe_id(session_id):
        return False
    session_dir = _root() / session_id
    if not session_dir.is_dir():
        return False
    shutil.rmtree(session_dir, ignore_errors=True)
    return not session_dir.exists()


def purge_all() -> None:
    """Wipe every upload. Call from the API's startup hook."""
    shutil.rmtree(_root(), ignore_errors=True)


def _prune_session_dir(session_dir: Path) -> None:
    """Drop a session directory once its last upload is gone."""
    if session_dir.is_dir() and not any(session_dir.iterdir()):
        shutil.rmtree(session_dir, ignore_errors=True)


def sweep(now: float) -> int:
    """Delete upload directories older than the TTL. Returns the count removed.

    ``now`` is supplied by the caller (a POSIX timestamp) so this module never
    reads the clock and the sweep stays deterministic under test.
    """
    root = _root()
    if not root.is_dir():
        return 0

    ttl = config.upload_ttl_seconds()
    removed = 0
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        for directory in sorted(session_dir.iterdir()):
            if not directory.is_dir():
                continue
            try:
                age = now - directory.stat().st_mtime
            except OSError:
                continue
            if age > ttl:
                shutil.rmtree(directory, ignore_errors=True)
                if not directory.exists():
                    removed += 1
        _prune_session_dir(session_dir)
    return removed
