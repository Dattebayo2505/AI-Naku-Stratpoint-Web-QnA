"""Inline local assets as ``data:`` URIs.

The renderer aborts every http(s) request (see ``pdf_service``), so an asset
that is not inlined is not in the PDF. Inlining is also what keeps a render
offline-safe: a `<link>` to a font CDN on a container with no egress does not
error, it *hangs*, and the whole point of the timeout is that we should not be
relying on it for the normal path.

Relative-path resolution against the template directory is deliberately not
supported: it would work in a checkout and break the moment the package is
installed as a wheel or run from a different cwd.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["data_uri"]

# mimetypes on Windows reads the registry, where .svg is frequently mapped to
# image/svg+xml but .webp is often missing entirely. Pin the ones we care about.
_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def data_uri(path: str | Path | None) -> str | None:
    """Read a local image and return it as a ``data:`` URI, or None.

    Returns None rather than raising for a missing or unreadable file: a
    missing logo must degrade to the template's built-in SVG mark, not fail a
    client's proposal.
    """
    if not path:
        return None
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as ex:
        log.warning("proposal asset %s could not be read (%s); falling back", p, ex)
        return None

    mime = _TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0]
    if not mime:
        log.warning("proposal asset %s has an unknown media type; falling back", p)
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
