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


def reset_conversation():
    """Clear messages and attachments, and generate a new session ID.

    The uploads are deleted server-side first, and deliberately *before* the
    session id rotates: the files are stored per session, so a delete sent with
    the new id would match nothing and silently strand confidential briefs on
    disk with a live upload id.

    ``api_client.delete_upload`` never raises, so a dead API cannot leave the
    user stuck in a conversation they are unable to reset.
    """
    session_id = st.session_state.get("session_id")
    for attachment in st.session_state.get("attachments") or []:
        api_client.delete_upload(session_id, attachment["upload_id"])

    st.session_state.messages = []
    st.session_state.attachments = []
    st.session_state.session_id = str(uuid.uuid4())
