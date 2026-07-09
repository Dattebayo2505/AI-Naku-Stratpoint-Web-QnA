import os
import streamlit as st
from stratpoint_rag.ui import state, api_client
from stratpoint_rag.ui.components import chat_transcript
from stratpoint_rag.ui.components import debug_panel
from stratpoint_rag.ui.components import resource_downloads

# Page config must be the first Streamlit command
st.set_page_config(page_title="A.I. Naku: Stratpoint Chatbot", layout="centered")

def main():
    # Initialize session state (messages, session_id)
    state.init_session_state()

    # --- Sidebar ---
    with st.sidebar:
        st.title("Debug Info")
        
        # API Connection Status
        is_connected = api_client.health_check()
        if is_connected:
            st.success(f"API: Connected\n\n({api_client.API_BASE_URL})")
        else:
            st.error(f"API: Unreachable\n\n({api_client.API_BASE_URL})")
            
        st.text_input("Session ID (Read-only)", value=st.session_state.session_id, disabled=True)
        
        if st.button("Reset conversation"):
            state.reset_conversation()
            st.rerun()

        enable_reasoning = st.toggle(
            "Enable reasoning",
            value=False,
            help="Let the model think step-by-step before answering (slower, more thorough).",
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
            
        # Call the API (streaming: live preview -> guardrail-safe final)
        with st.chat_message("assistant", avatar=os.path.join(ui_dir, "bot_avatar.svg")):
            try:
                history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.messages[:-1]
                ]

                placeholder = st.empty()
                preview = ""
                done_event: dict = {}

                with st.spinner("Thinking..."):
                    for event in api_client.stream_message(
                        session_id=st.session_state.session_id,
                        message=prompt,
                        history=history,
                    ):
                        etype = event.get("type")
                        if etype == "delta":
                            preview += event.get("text", "")
                            placeholder.markdown(preview + "▌")  # cursor while typing
                        elif etype == "done":
                            done_event = event
                        elif etype == "error":
                            raise api_client.APIError(event.get("detail", "stream error"))

                # done.answer is authoritative — output guardrails may have
                # redacted/blocked the streamed preview, so overwrite with it.
                answer = done_event.get("answer") or preview or "No answer provided."
                placeholder.markdown(answer)

                response_data = dict(done_event)
                response_data.pop("type", None)
                response_data["answer"] = answer

                resource_downloads.render(
                    response_data,
                    key_prefix=f"msg{len(st.session_state.messages)}",
                )
                debug_panel.render(response_data)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "raw_response": response_data
                })

            except api_client.APIError as e:
                st.error(str(e))

if __name__ == "__main__":
    main()
