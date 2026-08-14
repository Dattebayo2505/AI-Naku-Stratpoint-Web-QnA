import hashlib
import os
import threading
import time
import streamlit as st
from stratpoint_rag.ui import state, api_client, attachments as att
from stratpoint_rag.ui.components import chat_transcript
from stratpoint_rag.ui.components import debug_panel
from stratpoint_rag.ui.components import proposal_download
from stratpoint_rag.ui.components import resource_downloads

# Page config must be the first Streamlit command
st.set_page_config(page_title="A.I. Naku: Stratpoint Chatbot", layout="centered")

# A client-side convenience only. /upload is a real HTTP endpoint reachable
# without Streamlit, so the authoritative check is content-based, at the API.
ACCEPTED_TYPES = ["pdf", "pptx", "png", "jpg", "jpeg", "webp", "tiff"]

@st.dialog("Transcribe this document?", dismissible=False)
def _confirm_dialog(pending: dict):
    """Confirmation step between /upload and the expensive /upload/{id}/parse."""
    seconds = att.estimate_seconds(pending["pages"])
    st.write(f"**{pending['filename']}** — {pending['pages']} pages")
    st.caption(f"Transcription takes roughly {seconds}s. You can keep typing while it runs.")

    left, right = st.columns(2)
    # st.rerun() is what closes the dialog: a full script run tears the modal
    # down, whereas a button click on its own only reruns the dialog fragment.
    # `pending_upload` is already gone by now (the caller popped it), so neither
    # branch clears it — clearing it here is what used to re-arm the uploader.
    if left.button("Transcribe", type="primary", use_container_width=True):
        st.session_state.attachments = att.add(
            st.session_state.attachments,
            {**pending, "parsed": False, "transcribing": True},
        )
        state.start_background_parse(st.session_state.session_id, pending["upload_id"])
        st.rerun()
    if right.button("Cancel", use_container_width=True):
        api_client.delete_upload(st.session_state.session_id, pending["upload_id"])
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
        "Drop a PDF, a PowerPoint deck, or an image",
        type=ACCEPTED_TYPES,
        label_visibility="collapsed",
    )

    if uploaded is None:
        # The widget was cleared, so forget the file we already dealt with —
        # re-dropping the same bytes should ask again.
        st.session_state.settled_upload_hash = None
    else:
        data = uploaded.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        # Streamlit hands back the same file on every rerun, including each chat
        # message. Without a content gate the UI re-POSTs /upload every turn.
        #
        # The gate is "have we already offered this file to the user", NOT "is it
        # attached yet". Keying it off `pending_upload`/`attachments` re-armed the
        # upload on the very rerun a dialog button had just disarmed it — the
        # click cleared `pending_upload`, the attachment did not exist yet, so the
        # file was re-uploaded, `pending_upload` was re-set, and the dialog
        # reopened on every subsequent run with no way out but the window's X.
        if att.find_by_hash(st.session_state.attachments, digest) is not None:
            st.session_state.settled_upload_hash = digest  # already in the chips
        elif digest != st.session_state.get("settled_upload_hash"):
            try:
                meta = api_client.upload_file(
                    st.session_state.session_id, uploaded.name, data
                )
            except api_client.APIError as e:
                # Left unsettled on purpose: the next rerun retries the upload.
                st.error(str(e))
            else:
                st.session_state.settled_upload_hash = digest
                st.session_state.pending_upload = meta

    # Pop, don't peek. The dialog is opened on the run that created the upload
    # and never re-derived from lingering state — while it is open the only full
    # script run comes from its own st.rerun(), and a modal dismissed with the X
    # leaves no flag behind, so peeking made it reappear over the chat on the
    # next interaction. The dialog body reads `pending` from its argument, which
    # Streamlit replays on fragment reruns, so popping does not break the buttons.
    pending = st.session_state.pop("pending_upload", None)
    if pending:
        _confirm_dialog(pending)

    state.check_background_parses()
    _render_attachment_section()


@st.fragment(run_every=1)
def _render_polling_attachment_chips():
    if state.check_background_parses(join_timeout=0.0):
        st.rerun(scope="app")
    _render_attachment_chips()


def _render_attachment_section():
    if any(a.get("transcribing") for a in st.session_state.get("attachments") or []):
        _render_polling_attachment_chips()
    else:
        _render_attachment_chips()


def _render_attachment_chips():
    for attachment in list(st.session_state.attachments):
        row, remove_col = st.columns([5, 1])
        row.caption(att.chip_label(attachment))
        if remove_col.button("x", key=f"rm_{attachment['upload_id']}", help="Remove"):
            api_client.delete_upload(st.session_state.session_id, attachment["upload_id"])
            state.cancel_background_parse(attachment["upload_id"])
            st.session_state.attachments = att.remove(
                st.session_state.attachments, attachment["upload_id"]
            )
            st.rerun()

        if attachment.get("transcribing"):
            st.caption("⏳ Transcribing in background…")
        elif attachment.get("parse_error"):
            st.error(
                f"Transcription error: {attachment['parse_error']}",
                icon=":material/error:",
            )
        elif attachment.get("pages_failed"):
            st.warning(
                f"Pages {', '.join(str(p) for p in attachment['pages_failed'])} "
                "could not be read.",
                icon=":material/warning:",
            )
        # Fetched over HTTP, never read off disk: `markdown_path` names a file
        # on the API host, so stat-ing it here worked only when both processes
        # happened to share a filesystem and silently hid the panel otherwise.
        # Loaded lazily, inside the expander, so a rerun does not re-fetch a
        # 40k-character document the user never opened.
        if attachment.get("parsed") and attachment.get("markdown_path"):
            with st.expander("View transcription"):
                markdown = api_client.fetch_transcription(
                    st.session_state.session_id, attachment["upload_id"]
                )
                if markdown is None:
                    st.caption("The transcription is no longer available.")
                else:
                    st.markdown(markdown)


def main():
    # Initialize session state (messages, session_id, attachments)
    state.init_session_state()

    # --- Sidebar ---
    with st.sidebar:
        st.title("Session")

        st.text_input("Session ID (Read-only)", value=st.session_state.session_id, disabled=True)

        enable_reasoning = st.toggle(
            "Enable reasoning",
            value=False,
            help="Show the assistant's step-by-step reasoning. On direct questions this also makes it reason before answering (slower, more thorough).",
        )

        st.markdown("---")
        _render_brief_uploader()
        st.markdown("---")

        with st.expander("📊 Observability (LLMOps)"):
            if st.button("Refresh metrics"):
                st.session_state["_metrics"] = api_client.get_metrics()
            data = st.session_state.get("_metrics")
            if not data:
                st.caption("Press refresh — or the API is unreachable.")
            else:
                agg = data.get("aggregates", {})
                st.metric("Requests", agg.get("count", 0))
                # Named for what they mean, not for the statistic: "p50/p95" is
                # jargon to the non-specialist audience the value proposition
                # has to land with, and milliseconds read as a bigger number
                # than they are (3861.0 vs 3.9s). Both are shown, because a
                # median alone hides the slow tail and a p95 alone makes a
                # healthy system look broken.
                p50 = agg.get("latency_p50_ms") or 0
                p95 = agg.get("latency_p95_ms") or 0
                st.metric(
                    "Typical response", f"{p50 / 1000:.1f}s",
                    help="Median (p50) — half of all requests finish faster than this.",
                )
                st.metric(
                    "Slowest 1 in 20", f"{p95 / 1000:.1f}s",
                    help="95th percentile (p95) — the slow tail: brief transcription "
                         "and proposal generation.",
                )
                st.metric("Total tokens", agg.get("total_tokens", 0))
                cost = agg.get("total_cost_usd")
                st.metric("Est. cost (USD)", f"{cost:.4f}" if cost is not None else "0")
                gr = agg.get("grounded_rate")
                st.metric("Grounded rate", f"{gr:.0%}" if gr is not None else "n/a")
                st.metric("Error rate", f"{agg.get('error_rate', 0):.0%}")
                st.metric("Guardrail/routing intercepts", f"{agg.get('guardrail_fire_rate', 0):.0%}")
                st.caption("Includes greetings and clarification prompts, not only safety blocks.")

        with st.expander("🧪 Evals"):
            include_judge = st.checkbox(
                "Include LLM-as-judge (live, ~20s)", value=True,
                help="Scores real generated proposals with the LLM. Uncheck for a "
                     "~4s run with no model calls.",
            )
            if st.button("Run evals"):
                with st.spinner("Running eval layers…"):
                    st.session_state["_evals"] = api_client.get_evals(include_judge)
            evals = st.session_state.get("_evals")
            if not evals:
                st.caption("Press Run — or the API is unreachable.")
            else:
                st.caption(f"Measured {evals['ran_at']}")
                st.dataframe(
                    [
                        {
                            "Eval": r["name"],
                            "Pass": f"{r['passed']}/{r['total']}",
                            "Rate": f"{r['rate']:.2f}",
                            "Floor": f"{r['floor']:.2f}" if r["floor"] is not None else "—",
                            "Status": r["status"],
                        }
                        for r in evals["rows"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
                # The detail column carries WHY a row reads as it does ("8
                # ungrounded", "2 of 10 calls failed"). Dropping it would turn a
                # partially-failed judge run into a clean-looking score.
                for r in evals["rows"]:
                    if r["detail"]:
                        st.caption(f"**{r['name']}** — {r['detail']}")
                if evals["ok"]:
                    st.success("All layers at or above their committed floor.")
                else:
                    st.error("A layer is below its floor.")

        if st.button("Reset conversation"):
            state.reset_conversation()
            st.rerun()

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
                            a["upload_id"]
                            for a in st.session_state.attachments
                            if a.get("parsed") and not a.get("transcribing")
                        ],
                    )
                    
                    answer = response_data.get("answer", "No answer provided.")
                    st.markdown(answer)

                    # Download controls (top result eager, rest lazy). The
                    # assistant message is appended just below, so its index
                    # will be the current messages length.
                    key_prefix = f"msg{len(st.session_state.messages)}"
                    resource_downloads.render(response_data, key_prefix=key_prefix)

                    # Fired here, on the turn that produced it, not from inside
                    # the component: Streamlit re-runs the whole script on every
                    # widget interaction, so a toast raised during render would
                    # pop again on each keystroke.
                    if state.remember_proposal(response_data):
                        st.toast("Your proposal PDF is ready.", icon="🧾")
                    proposal_download.render(response_data, key_prefix=key_prefix)

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
