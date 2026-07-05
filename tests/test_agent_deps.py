def test_agent_dependencies_importable():
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    import langchain_core  # noqa: F401
    import langgraph  # noqa: F401
    from langchain_nvidia_ai_endpoints import ChatNVIDIA  # noqa: F401
    from langgraph.prebuilt import create_react_agent  # noqa: F401
