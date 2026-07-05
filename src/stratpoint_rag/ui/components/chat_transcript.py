import streamlit as st
from stratpoint_rag.ui.components import debug_panel

def render():
    """Render the chat transcript and debug panels."""
    if "messages" not in st.session_state:
        return
        
    for msg in st.session_state.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        with st.chat_message(role):
            st.markdown(content)
            
            # If it's an assistant message and we have raw response data, show the debug panel
            if role == "assistant" and "raw_response" in msg:
                debug_panel.render(msg["raw_response"])
