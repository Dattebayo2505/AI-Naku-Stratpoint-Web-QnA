import uuid

import streamlit as st

from stratpoint_rag.ui import api_client


def init_session_state():
    """Initialize session state variables if they don't exist."""
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    if "attachments" not in st.session_state:
        st.session_state.attachments = []

    # The most recent proposal this session generated. Tracked separately from
    # the message it arrived on so the sidebar and a toast can reach it without
    # walking the transcript; the transcript still renders its own button from
    # each message's stored raw_response, which is what survives a rerun.
    if "proposal_pdf_path" not in st.session_state:
        st.session_state.proposal_pdf_path = None

    if "proposal_download_url" not in st.session_state:
        st.session_state.proposal_download_url = None


def remember_proposal(raw_response: dict) -> bool:
    """Record a proposal from an agent response. True when this is a new one.

    Returns a boolean rather than raising a toast itself so the caller decides
    whether the user is looking — Streamlit reruns the whole script on every
    widget interaction, and a toast fired from here would reappear on each one.
    """
    data = raw_response.get("proposal_data") or {}
    if not isinstance(data, dict):
        data = data.model_dump()
    pdf = data.get("pdf")
    if not pdf:
        return False
    if not isinstance(pdf, dict):
        pdf = pdf.model_dump()

    url = pdf.get("download_url")
    if not url or url == st.session_state.get("proposal_download_url"):
        return False

    st.session_state.proposal_download_url = url
    st.session_state.proposal_pdf_path = pdf.get("pdf_path")
    return True


def reset_conversation():
    """Clear messages, attachments, and proposals, and generate a new session ID.

    The server-side files are deleted first, and deliberately *before* the
    session id rotates: they are stored per session, so a delete sent with the
    new id would match nothing and silently strand confidential briefs — and
    now client quotes — on disk with a live id.

    ``api_client.delete_upload`` / ``delete_proposals`` never raise, so a dead
    API cannot leave the user stuck in a conversation they are unable to reset.
    """
    session_id = st.session_state.get("session_id")
    for attachment in st.session_state.get("attachments") or []:
        api_client.delete_upload(session_id, attachment["upload_id"])
    api_client.delete_proposals(session_id)

    st.session_state.messages = []
    st.session_state.attachments = []
    st.session_state.proposal_pdf_path = None
    st.session_state.proposal_download_url = None
    st.session_state.session_id = str(uuid.uuid4())
