"""Download + preview controls for a generated proposal PDF.

Fetched over HTTP, never read off disk. The UI and the API are two processes
that only sometimes share a filesystem — ``STRATPOINT_API_URL`` explicitly
supports running Streamlit locally against the LXC — and the moment this reads
``proposal_data.pdf.pdf_path`` directly it works on the developer's laptop and
shows a broken button in production. The same rule the upload path follows.

The preview shows the **HTML twin**, not the PDF: Streamlit renders components
inside a sandboxed iframe, and Chrome refuses to load a PDF from a ``data:``
URI in one. The HTML is the same document one render stage earlier.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from stratpoint_rag.ui import api_client

__all__ = ["render"]

_PREVIEW_HEIGHT = 900


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch(url: str) -> bytes | None:
    """Cached so a rerun — and the transcript re-render on every keystroke —
    never refetches the same proposal."""
    return api_client.fetch_proposal(url)


def _pdf_info(raw_response: dict) -> dict | None:
    """Pull the PDF block out of an AgentResult, tolerating both shapes.

    ``proposal_data`` round-trips through JSON as a dict, but a caller holding
    the model itself is a legitimate shape too, and the transcript replays
    whatever it stored.
    """
    data = raw_response.get("proposal_data")
    if data is None:
        return None
    if not isinstance(data, dict):
        data = data.model_dump()
    pdf = data.get("pdf")
    if pdf is None:
        return None
    if not isinstance(pdf, dict):
        pdf = pdf.model_dump()
    return pdf if pdf.get("download_url") else None


def render(raw_response: dict, key_prefix: str) -> None:
    """Render the download button and preview for ``raw_response``'s proposal.

    ``key_prefix`` must be stable and unique per assistant message so Streamlit
    widget keys survive reruns — the same contract ``resource_downloads`` uses.
    """
    pdf = _pdf_info(raw_response)
    if pdf is None:
        return

    url = pdf["download_url"]
    filename = url.rsplit("/", 1)[-1]

    st.markdown("**🧾 Proposal**")
    with st.spinner("Preparing your proposal…"):
        data = _fetch(url)

    if data is None:
        # An expired TTL sweep or a restarted API is the common cause, and it is
        # worth saying so: the alternative is a dead button the visitor keeps
        # clicking.
        st.warning(
            "The proposal is no longer available on the server. Ask for it again "
            "to regenerate it.",
            icon=":material/link_off:",
        )
        return

    st.download_button(
        "⬇️ Download PDF proposal",
        data=data,
        file_name=filename,
        mime="application/pdf",
        key=f"dl_prop_{key_prefix}",
        type="primary",
    )

    with st.expander("Preview proposal"):
        html = _fetch(url.replace(".pdf", ".html"))
        if html is None:
            st.caption("Preview unavailable — the download above still works.")
        else:
            components.html(
                html.decode("utf-8", errors="replace"),
                height=_PREVIEW_HEIGHT,
                scrolling=True,
            )
