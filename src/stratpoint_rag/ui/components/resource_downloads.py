"""Render download controls for find_resource results inside the chat bubble.

The top result is fetched eagerly (its download button is ready immediately);
any further results are fetched lazily, only once the user clicks to prepare
them. When a server-side fetch is refused (non-public host) or fails, we fall
back to an external link so the user can still reach the file.
"""
from __future__ import annotations

import streamlit as st

from stratpoint_rag.ui.resource_fetch import filename_for, mime_for, safe_fetch


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch(url: str) -> bytes | None:
    """Cached wrapper so a rerun (or re-render in the transcript) never refetches."""
    return safe_fetch(url)


def render(raw_response: dict, key_prefix: str) -> None:
    """Render download buttons for `raw_response['resources']`.

    `key_prefix` must be stable and unique per assistant message so Streamlit
    widget keys and the per-resource "prepared" flags survive reruns.
    """
    resources = raw_response.get("resources", []) or []
    if not resources:
        return

    st.markdown("**📄 Downloadable resources**")
    for i, res in enumerate(resources):
        url = res.get("url", "")
        title = res.get("title") or filename_for(url) or "resource"
        state_key = f"{key_prefix}_res{i}"

        # Top result (i == 0) is prepared eagerly; the rest wait for a click.
        if state_key not in st.session_state:
            st.session_state[state_key] = i == 0

        if not st.session_state[state_key]:
            if st.button(f"Prepare download: {title}", key=f"prep_{state_key}"):
                st.session_state[state_key] = True
                st.rerun()
            continue

        with st.spinner(f"Preparing {title}…"):
            data = _fetch(url)

        if data is not None:
            st.download_button(
                f"⬇️ {title}",
                data=data,
                file_name=filename_for(url),
                mime=mime_for(url),
                key=f"dl_{state_key}",
            )
        elif url:
            st.link_button(f"⬇️ {title} (open externally)", url, key=f"lb_{state_key}")
