import os
import requests
from typing import Any, Dict

API_BASE_URL = os.environ.get("STRATPOINT_API_URL", "http://localhost:8000").rstrip("/")

# /chat and /upload/{id}/parse have very different latency profiles and must not
# share a ceiling. A 20-page brief is ~100s sequential (up to 20 vision calls),
# which would blow the chat timeout; parse therefore carries its own, matching
# LLM_TIMEOUT. Upload itself calls no model at all and stays snappy.
CHAT_TIMEOUT = 120
UPLOAD_TIMEOUT = 30
PARSE_TIMEOUT = 300


class APIError(Exception):
    """Custom exception for API errors."""
    pass


def health_check() -> bool:
    """Check if the API is reachable."""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _handle(exc: Exception, *, what: str) -> APIError:
    """Turn a requests exception into a user-facing APIError."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        return APIError(f"Cannot connect to the API at {API_BASE_URL}. Is it running?")
    if isinstance(exc, requests.exceptions.Timeout):
        return APIError(f"The API request timed out while {what}.")
    if isinstance(exc, requests.exceptions.HTTPError):
        msg = f"API returned an error: {exc.response.status_code}"
        try:
            detail = exc.response.json().get("detail", "")
            if detail:
                msg += f" - {detail}"
        except Exception:
            pass
        return APIError(msg)
    return APIError(f"An unexpected error occurred while {what}: {exc}")


def send_message(
    session_id: str,
    message: str,
    history: list[dict] = None,
    enable_reasoning: bool = False,
    attachments: list[str] | None = None,
) -> Dict[str, Any]:
    """Send a message to the chat API and return the parsed JSON response."""
    payload = {
        "message": message,
        "session_id": session_id,
        "enable_reasoning": enable_reasoning,
    }
    if history is not None:
        payload["history"] = history
    if attachments:
        payload["attachments"] = attachments

    try:
        response = requests.post(f"{API_BASE_URL}/chat", json=payload, timeout=CHAT_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise _handle(e, what="waiting for the agent") from None


def upload_file(session_id: str, filename: str, data: bytes) -> Dict[str, Any]:
    """Store a brief and get its page count back. No model runs here."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/upload",
            files={"file": (filename, data)},
            data={"session_id": session_id},
            timeout=UPLOAD_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise _handle(e, what="uploading the file") from None


def parse_upload(session_id: str, upload_id: str) -> Dict[str, Any]:
    """Transcribe a stored brief (hop 1). Slow by nature — see PARSE_TIMEOUT."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/upload/{upload_id}/parse",
            params={"session_id": session_id},
            timeout=PARSE_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise _handle(e, what="transcribing the document") from None


def fetch_transcription(session_id: str, upload_id: str) -> str | None:
    """Fetch hop 1's Markdown for an upload, or None.

    Returns None rather than raising, like ``fetch_proposal``: a swept, purged
    or not-yet-parsed upload is an expected outcome, and the caller turns it
    into "no panel" rather than an error banner over the sidebar.
    """
    try:
        response = requests.get(
            f"{API_BASE_URL}/upload/{upload_id}/transcription",
            params={"session_id": session_id},
            timeout=UPLOAD_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
    except requests.RequestException:
        return None


def fetch_proposal(download_url: str) -> bytes | None:
    """Fetch a generated proposal (or its HTML twin) by the URL the agent returned.

    Returns None rather than raising: a swept or purged proposal is an expected
    outcome, and the caller turns it into "ask for it again" rather than into an
    error banner over the whole chat.

    Only the path is taken from ``download_url``; the host is always this
    client's configured API. The URL originates in a tool result the model has
    seen, so treating it as a fetchable address would let a prompt-injected
    brief steer the browser at a host of its choosing.
    """
    path = "/" + download_url.split("://", 1)[-1].split("/", 1)[-1].lstrip("/")
    try:
        response = requests.get(f"{API_BASE_URL}{path}", timeout=UPLOAD_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None


def delete_proposals(session_id: str) -> bool:
    """Best-effort delete of a session's proposals. Never raises — this runs on
    reset, and a dead API must not trap the user in a conversation."""
    try:
        response = requests.delete(
            f"{API_BASE_URL}/proposals/{session_id}", timeout=UPLOAD_TIMEOUT
        )
        response.raise_for_status()
        return bool(response.json().get("deleted"))
    except requests.RequestException:
        return False


def delete_upload(session_id: str, upload_id: str) -> bool:
    """Best-effort delete. Never raises: this runs on reset and on the sidebar
    '(x)', and a dead API must not take the whole UI down with it."""
    try:
        response = requests.delete(
            f"{API_BASE_URL}/upload/{upload_id}",
            params={"session_id": session_id},
            timeout=UPLOAD_TIMEOUT,
        )
        response.raise_for_status()
        return bool(response.json().get("deleted"))
    except requests.RequestException:
        return False
