import streamlit as st
from typing import Dict, Any

def render(raw_response: Dict[str, Any]):
    """Render the 'Under the hood' debug panel for an assistant turn."""
    with st.expander("Under the hood", expanded=False):
        # 1. Sources
        st.markdown("**Sources**")
        citations = raw_response.get("citations", [])
        if citations:
            for idx, citation in enumerate(citations, 1):
                title = citation.get("title", "Untitled")
                url = citation.get("url", "#")
                st.markdown(f"{idx}. [{title}]({url})")
        else:
            st.markdown("*Not available*")
            
        st.divider()

        # 2. Agent trace
        st.markdown("**Agent trace**")
        trace = raw_response.get("trace", [])
        if trace:
            for step in trace:
                step_type = step.get("type", "unknown")
                if step_type == "thought":
                    st.markdown(f"**Thought:** {step.get('content', '')}")
                elif step_type == "action":
                    st.markdown(f"**Tool Call:** `{step.get('tool', 'unknown')}`")
                    st.json(step.get("tool_input", {}))
                elif step_type == "observation":
                    with st.expander(f"Observation: {step.get('tool', 'unknown')}"):
                        st.text(step.get('content', ''))
                elif step_type == "answer":
                    st.markdown(f"**Answer generated.**")
                else:
                    st.markdown(f"**{step_type.capitalize()}:** {step.get('content', '')}")
        else:
            st.markdown("*Not available*")
            
        st.divider()
        
        # 3. Grounding / guardrail status
        st.markdown("**Grounding / guardrail status**")
        is_grounded = raw_response.get("is_grounded")
        confidence = raw_response.get("confidence")
        guardrail_reason = raw_response.get("guardrail_reason")
        
        has_status = False
        
        if is_grounded is not None:
            has_status = True
            if is_grounded:
                st.success(f"Grounded (Confidence: {confidence if confidence is not None else 'N/A'})")
            else:
                st.warning(f"Not Grounded (Confidence: {confidence if confidence is not None else 'N/A'})")
                
        if guardrail_reason:
            has_status = True
            st.error(f"Guardrail/Refusal: {guardrail_reason}")
            
        if not has_status:
            st.markdown("*Not available*")
            
        st.divider()
        
        # 4. Raw response (JSON)
        st.markdown("**Raw response**")
        st.json(raw_response)
