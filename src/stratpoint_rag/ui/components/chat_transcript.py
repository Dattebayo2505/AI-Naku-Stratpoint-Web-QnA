import os
import streamlit as st
from stratpoint_rag.ui.components import debug_panel
from stratpoint_rag.ui.components import resource_downloads

def render():
    """Render the chat transcript and debug panels."""
    if "messages" not in st.session_state:
        return
        
    for idx, msg in enumerate(st.session_state.messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Use custom SVG files for avatars
        ui_dir = os.path.dirname(os.path.dirname(__file__))
        if role == "user":
            avatar = os.path.join(ui_dir, "user_avatar.svg")
        else:
            avatar = os.path.join(ui_dir, "bot_avatar.svg")
            
        with st.chat_message(role, avatar=avatar):
            st.markdown(content)
            
            # If it's an assistant message and we have raw response data, show
            # the download controls + debug panel. key_prefix uses the message
            # index so widget keys / prepared-flags match app.py's live render.
            if role == "assistant" and "raw_response" in msg:
                resource_downloads.render(msg["raw_response"], key_prefix=f"msg{idx}")
                debug_panel.render(msg["raw_response"])
