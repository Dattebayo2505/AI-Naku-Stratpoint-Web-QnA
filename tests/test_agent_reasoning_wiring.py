import langchain_nvidia_ai_endpoints as nv
import langchain.agents as la

from stratpoint_rag.agent import agent


def _patch(monkeypatch):
    captured = []

    class _FakeChat:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(nv, "ChatNVIDIA", _FakeChat)
    monkeypatch.setattr(la, "create_agent", lambda llm, tools, system_prompt=None: object())
    monkeypatch.setattr(agent.config, "nvidia_api_key", lambda: "test-key")
    # Reset the module-level cache so the test controls it.
    monkeypatch.setattr(agent, "_agents", {}, raising=False)
    return captured


def test_build_agent_enables_thinking_via_chat_template_kwargs(monkeypatch):
    captured = _patch(monkeypatch)
    agent._build_agent(enable_reasoning=True)
    # LIVE-CONFIRMED: constructor kwarg chat_template_kwargs (NOT extra_body).
    assert captured[-1].get("chat_template_kwargs") == {"enable_thinking": True}


def test_build_agent_thinking_off_disables_it(monkeypatch):
    captured = _patch(monkeypatch)
    agent._build_agent(enable_reasoning=False)
    assert captured[-1].get("chat_template_kwargs") == {"enable_thinking": False}


def test_get_agent_caches_per_flag(monkeypatch):
    _patch(monkeypatch)
    on1 = agent._get_agent(True)
    on2 = agent._get_agent(True)
    off = agent._get_agent(False)
    assert on1 is on2      # same flag -> cached instance
    assert on1 is not off  # distinct flag -> distinct instance


def test_run_agent_forwards_enable_reasoning_to_get_agent(monkeypatch):
    seen = {}

    class _FakeAgent:
        def invoke(self, payload, config=None):
            from langchain_core.messages import AIMessage
            return {"messages": [AIMessage(content="ok")]}

    def fake_get_agent(enable_reasoning=False):
        seen["enable_reasoning"] = enable_reasoning
        return _FakeAgent()

    monkeypatch.setattr(agent, "_get_agent", fake_get_agent)
    agent.run_agent("hi", enable_reasoning=True)
    assert seen["enable_reasoning"] is True
