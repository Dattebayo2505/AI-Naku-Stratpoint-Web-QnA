import hashlib
import os
import streamlit as st
from stratpoint_rag.ui import state, api_client, attachments as att
from stratpoint_rag.ui.components import chat_transcript
from stratpoint_rag.ui.components import debug_panel
from stratpoint_rag.ui.components import resource_downloads

# Page config must be the first Streamlit command
st.set_page_config(page_title="A.I. Naku: Stratpoint Chatbot", layout="centered")

ACCEPTED_TYPES = ["pdf", "png", "jpg", "jpeg", "webp", "tiff"]


@st.dialog("Transcribe this document?")
def _confirm_dialog(pending: dict):
    """Confirmation step between /upload and the expensive /upload/{id}/parse."""
    seconds = att.estimate_seconds(pending["pages"])
    st.write(f"**{pending['filename']}** — {pending['pages']} pages")
    st.caption(f"Transcription takes roughly {seconds}s. You can keep typing while it runs.")

    left, right = st.columns(2)
    if left.button("Transcribe", type="primary", use_container_width=True):
        st.session_state.pending_upload = None
        st.session_state.confirmed_upload = pending
        st.rerun()
    if right.button("Cancel", use_container_width=True):
        api_client.delete_upload(st.session_state.session_id, pending["upload_id"])
        st.session_state.pending_upload = None
        st.rerun()


def _render_brief_uploader():
    """st.file_uploader, not st.chat_input(accept_file=True).

    The chat-input uploader is prettier, but the file only reaches our code when
    the user hits send — so the 25-100s transcription would happen *after*
    submission, while they wait on a reply. That is precisely the experience
    eager parsing exists to eliminate. file_uploader fires the moment the file
    is dropped, so transcription runs while the user is still typing.
    """
    st.subheader("Client brief")
    uploaded = st.file_uploader(
        "Drop a PDF or image", type=ACCEPTED_TYPES, label_visibility="collapsed"
    )

    if uploaded is not None:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        # Streamlit hands back the same file on every rerun, including each chat
        # message. Without this hash guard the UI re-POSTs /upload every turn.
        known = att.find_by_hash(st.session_state.attachments, digest)
        pending = st.session_state.get("pending_upload")
        if known is None and (pending is None or pending["sha256"] != digest):
            try:
                meta = api_client.upload_file(
                    st.session_state.session_id, uploaded.name, data
                )
                st.session_state.pending_upload = meta
            except api_client.APIError as e:
                st.error(str(e))

    pending = st.session_state.get("pending_upload")
    if pending:
        _confirm_dialog(pending)

    confirmed = st.session_state.pop("confirmed_upload", None)
    if confirmed:
        _parse_confirmed(confirmed)

    _render_attachment_chips()


def _parse_confirmed(meta: dict):
    with st.spinner(f"Transcribing {meta['filename']}..."):
        try:
            result = api_client.parse_upload(st.session_state.session_id, meta["upload_id"])
        except api_client.APIError as e:
            st.error(str(e))
            return

    st.session_state.attachments = att.add(
        st.session_state.attachments, {**meta, **result, "parsed": True}
    )


def _render_attachment_chips():
    for attachment in list(st.session_state.attachments):
        row, remove_col = st.columns([5, 1])
        row.caption(att.chip_label(attachment))
        if remove_col.button("x", key=f"rm_{attachment['upload_id']}", help="Remove"):
            api_client.delete_upload(st.session_state.session_id, attachment["upload_id"])
            st.session_state.attachments = att.remove(
                st.session_state.attachments, attachment["upload_id"]
            )
            st.rerun()

        if attachment.get("pages_failed"):
            st.warning(
                f"Pages {', '.join(str(p) for p in attachment['pages_failed'])} "
                "could not be read.",
                icon=":material/warning:",
            )
        path = attachment.get("markdown_path")
        if path and os.path.isfile(path):
            with st.expander("View transcription"):
                st.markdown(open(path, encoding="utf-8").read())


def main():
    # Initialize session state (messages, session_id, attachments)
    state.init_session_state()

    # --- Sidebar ---
    with st.sidebar:
        st.title("Session")

        # API Connection Status
        is_connected = api_client.health_check()
        if is_connected:
            st.success(f"API: Connected\n\n({api_client.API_BASE_URL})")
        else:
            st.error(f"API: Unreachable\n\n({api_client.API_BASE_URL})")

        st.markdown("---")
        _render_brief_uploader()
        st.markdown("---")

        st.text_input("Session ID (Read-only)", value=st.session_state.session_id, disabled=True)

        if st.button("Reset conversation"):
            state.reset_conversation()
            st.rerun()

        enable_reasoning = st.toggle(
            "Enable reasoning",
            value=False,
            help="Show the assistant's step-by-step reasoning. On direct questions this also makes it reason before answering (slower, more thorough).",
        )

        st.markdown("---")
        st.markdown("*Theme: edit `.streamlit/config.toml`*")

    # --- Main Chat Area ---
    st.title("A.I. Naku: Stratpoint Chatbot")
    
    # Render transcript
    chat_transcript.render()
    
    # Chat Input
    if prompt := st.chat_input("Ask a question about Stratpoint..."):
        # Append user message to state
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Show the user's message immediately
        ui_dir = os.path.dirname(__file__)
        with st.chat_message("user", avatar=os.path.join(ui_dir, "user_avatar.svg")):
            st.markdown(prompt)
            
        # Call the API
        with st.chat_message("assistant", avatar=os.path.join(ui_dir, "bot_avatar.svg")):
            with st.spinner("Thinking..."):
                try:
                    # Construct history for the API (only user/assistant roles, exclude the current prompt)
                    history = [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.messages[:-1]
                    ]
                    
                    response_data = api_client.send_message(
                        session_id=st.session_state.session_id,
                        message=prompt,
                        history=history,
                        enable_reasoning=enable_reasoning,
                        attachments=[
                            a["upload_id"] for a in st.session_state.attachments
                        ],
                    )
                    
                    answer = response_data.get("answer", "No answer provided.")
                    st.markdown(answer)

                    # Download controls (top result eager, rest lazy). The
                    # assistant message is appended just below, so its index
                    # will be the current messages length.
                    resource_downloads.render(
                        response_data,
                        key_prefix=f"msg{len(st.session_state.messages)}",
                    )

                    # Render the debug panel immediately
                    debug_panel.render(response_data)
                    
                    # Append assistant response to state
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "raw_response": response_data
                    })
                    
                except api_client.APIError as e:
                    st.error(str(e))

if __name__ == "__main__":
    main()
