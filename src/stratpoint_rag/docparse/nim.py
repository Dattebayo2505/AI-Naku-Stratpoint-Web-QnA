"""NVIDIA NIM vision client — the production ``VisionClient``.

httpx, no ``openai`` SDK, matching ``rag/answer.py``'s conventions.

**The payload form is mandatory, not stylistic.** The endpoint accepts two
shapes and returns HTTP 200 for both, but only the OpenAI multimodal form
routes the image to the vision encoder::

    content: [{"type": "text", ...}, {"type": "image_url", {"url": "data:..."}}]   # sees it
    content: "<prompt> <img src=\\"data:image/jpeg;base64,...\\" />"                 # does NOT

The HTML-``<img>`` form is a trap: the base64 is tokenized as plain text and
the model hallucinates a fluent description of an image it never received. On
the same spaceship test image the OpenAI form returned *"a spaceship navigating
through the rings of Saturn"* while the HTML-img form returned *"a woman with
long, flowing hair sitting on a rock."* Token accounting confirms the
mechanism: an 11,268-char base64 billed **8,058** prompt tokens under HTML-img
(~1.4 chars/token — text tokenization) versus **1,628** under the OpenAI form.
That form belongs to the legacy ``ai.api.nvidia.com/v1/vlm/...`` NVCF
endpoints; it does not belong here.

Two corollaries worth not re-deriving:

- **There is no ~180 KB payload cap on this endpoint.** A 25.6 MB base64 body
  returned 200 OK. The widely-cited 180 KB figure is real but belongs to the
  legacy NVCF VLM endpoints, where base64 counts against the 131,072-token
  *text* context (180 KB of base64 ~= 128k tokens — exactly where the number
  comes from). Do not build a downscale ladder, an oversize fallback, or an
  asset-upload path.
- **One image per request.** Two gets ``"At most 1 image(s) may be provided in
  one prompt."`` — HTTP 400 in 0.82s, refused before inference.
"""

from __future__ import annotations

import base64

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from stratpoint_rag.docparse import config

__all__ = ["NimVisionClient"]

# 429 is the one that matters: 40 requests/min per model, and a 20-page parse
# burns 20 of them. 5xx is transient endpoint load. 4xx is not retried — a 400
# means the payload is wrong, and retrying only burns more of the rate limit.
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Deliberately terse. The rules live in the system message; anything long here
# competes with the page for the model's attention and risks being transcribed.
_USER_TURN = "Transcribe this page."


class _Retryable(Exception):
    """Wraps a response worth trying again, so tenacity can select on it."""

    def __init__(self, error: httpx.HTTPStatusError) -> None:
        super().__init__(str(error))
        self.error = error


class NimVisionClient:
    """Transcribes one page image per call. Returns ``(markdown, usage)``.

    Usage is returned, never accumulated — page work runs on a thread pool and
    ``llmops`` is thread-local. See ``docparse/clients.py``.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 4,
        backoff_multiplier: float = 1.0,
        backoff_max: float = 20.0,
    ) -> None:
        self.max_attempts = max_attempts
        self._backoff_multiplier = backoff_multiplier
        self._backoff_max = backoff_max

    def describe(self, image_jpeg: bytes, prompt: str) -> tuple[str, dict]:
        key = config.nvidia_vision_api_key()
        if not key:
            raise RuntimeError(
                "No vision API key: set NVIDIA_VISION_API_KEY or NVIDIA_API_KEY "
                "(see .envexample)"
            )

        data_uri = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        body = {
            "model": config.vision_model(),
            # The instructions go in a SYSTEM message, never beside the image.
            # Observed live: with the rules in the same user turn, the model
            # transcribed the page's real table and then kept going — emitting
            # "### Rules" followed by every instruction bullet verbatim, as
            # though the prompt were printed on the page. Separating the roles
            # is what stops the prompt leaking into the artifact.
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _USER_TURN},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "max_tokens": config.MAX_TOKENS,
            "temperature": config.TEMPERATURE,
            "stream": False,
        }

        retryer = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(
                multiplier=self._backoff_multiplier, max=self._backoff_max
            ),
            retry=retry_if_exception_type(_Retryable),
            reraise=True,  # surface the last _Retryable, not tenacity's RetryError
        )
        try:
            return retryer(self._post_once, body, key)
        except _Retryable as e:
            raise e.error from None

    def _post_once(self, body: dict, key: str) -> tuple[str, dict]:
        resp = httpx.post(
            f"{config.nvidia_base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=config.VISION_TIMEOUT,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if resp.status_code in _RETRY_STATUSES:
                raise _Retryable(e) from None
            raise

        data = resp.json()
        return data["choices"][0]["message"]["content"], data.get("usage") or {}
