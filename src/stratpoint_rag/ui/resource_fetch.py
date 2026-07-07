"""Safe server-side fetching of externally-hosted resource files.

`find_resource` returns links to PDFs / office docs on ARBITRARY external hosts
(not just stratpoint.com), so a host allowlist is not an option. Instead we
refuse non-public targets (loopback / private / link-local / reserved) so the
fetch cannot be used as an SSRF pivot, cap the body size to avoid OOM, and let
callers fall back to the plain external link when a fetch is refused or fails.

This module is deliberately Streamlit-free so it can be unit-tested offline;
the caching + rendering live in components/resource_downloads.py.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import unquote, urlparse

import requests

# Generous cap purely to avoid OOM on a runaway download — not a product limit.
MAX_BYTES = 50 * 1024 * 1024
_TIMEOUT = 30

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _is_public_http(url: str) -> bool:
    """True only for http(s) URLs whose host resolves entirely to public IPs.

    Note: there is an inherent TOCTOU gap — we resolve here and `requests`
    re-resolves on connect, so a DNS-rebinding host could still slip past. For
    corpus-curated links that trade-off is acceptable; a proxy endpoint that
    pins the resolved IP would be the hardening step if the threat model grows.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if not addr.is_global:  # rejects private/loopback/link-local/reserved
            return False
    return True


def filename_for(url: str) -> str:
    """Best-effort download filename from the URL path."""
    name = unquote(os.path.basename(urlparse(url).path)).strip()
    return name or "download"


def mime_for(url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def safe_fetch(url: str, *, max_bytes: int = MAX_BYTES) -> bytes | None:
    """Fetch the file bytes, or None if the target is non-public, too large, or
    the request fails. Never raises — callers fall back to the external link.
    """
    if not _is_public_http(url):
        return None
    try:
        with requests.get(url, timeout=_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException:
        return None
