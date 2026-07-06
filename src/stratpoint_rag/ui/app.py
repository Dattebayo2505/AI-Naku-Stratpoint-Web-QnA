import os
import streamlit as st
from stratpoint_rag.ui import state, api_client
from stratpoint_rag.ui.components import chat_transcript
from stratpoint_rag.ui.components import debug_panel

# Page config must be the first Streamlit command
st.set_page_config(page_title="Stratpoint Client Q&A Chatbot", layout="centered")

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
            
        st.markdown("---")
        st.markdown("*Theme: edit `.streamlit/config.toml`*")

    # --- Main Chat Area ---
    st.title("Stratpoint Client Q&A Chatbot")
    
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
                        history=history
                    )
                    
                    answer = response_data.get("answer", "No answer provided.")
                    st.markdown(answer)
                    
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
