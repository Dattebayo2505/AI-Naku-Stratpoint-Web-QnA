import uuid
import streamlit as st

def init_session_state():
    """Initialize session state variables if they don't exist."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
        
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

def reset_conversation():
    """Clear messages and generate a new session ID."""
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())
