from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from stratpoint_rag.agent import agent


class _FakeAgent:
    """Stands in for a compiled create_react_agent; records the input, returns canned messages."""
    def __init__(self, messages):
        self._messages = messages
        self.seen = None

    def invoke(self, payload, config=None):
        self.seen = payload
        return {"messages": self._messages}


def test_run_agent_returns_agentresult_from_injected_agent():
    fake = _FakeAgent([
        HumanMessage(content="services?"),
        AIMessage(content="We build software, cloud, data, and AI solutions."),
    ])
    result = agent.run_agent("What services do you offer?", agent=fake)
    assert result.answer == "We build software, cloud, data, and AI solutions."
    # the user message is threaded into the agent payload
    assert fake.seen["messages"][-1] == ("user", "What services do you offer?")


def test_run_agent_threads_history():
    fake = _FakeAgent([AIMessage(content="ok")])
    agent.run_agent(
        "and pricing?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        agent=fake,
    )
    assert fake.seen["messages"] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "and pricing?"),
    ]


def test_agent_package_reexports():
    import stratpoint_rag.agent as pkg
    assert hasattr(pkg, "run_agent") and hasattr(pkg, "AgentResult")


def test_build_agent_sets_generous_timeout(monkeypatch):
    """Regression: the NIM client must be built with a timeout well above the
    ~40s steady-state agent latency, so a transient spike doesn't 502."""
    import langchain_nvidia_ai_endpoints as nv
    import langgraph.prebuilt as lp

    captured = {}

    class _FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(nv, "ChatNVIDIA", _FakeChat)
    monkeypatch.setattr(lp, "create_react_agent", lambda llm, tools, prompt=None: object())
    monkeypatch.setattr(agent.config, "nvidia_api_key", lambda: "test-key")

    agent._build_agent()
    assert captured.get("timeout", 0) >= 120
