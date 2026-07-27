"""run_agent is a thin seam over react.run_react."""
from stratpoint_rag.agent import agent


class ScriptedChat:
    def __init__(self, *turns):
        self._turns = list(turns)
        self.calls = []

    def __call__(self, messages, stop):
        self.calls.append({"messages": [dict(m) for m in messages], "stop": list(stop)})
        return self._turns.pop(0)


def test_run_agent_returns_agentresult_from_injected_chat():
    chat = ScriptedChat("Answer: We build software, cloud, data, and AI solutions.")
    result = agent.run_agent("What services do you offer?", chat=chat)

    assert result.answer == "We build software, cloud, data, and AI solutions."
    assert chat.calls[0]["messages"][-1] == {
        "role": "user", "content": "What services do you offer?",
    }


def test_run_agent_threads_history():
    chat = ScriptedChat("Answer: ok")
    agent.run_agent(
        "and pricing?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        chat=chat,
    )
    # [0] is the system prompt; history follows, then the new message.
    assert chat.calls[0]["messages"][1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "and pricing?"},
    ]


def test_run_agent_forwards_enable_reasoning():
    chat = ScriptedChat("Thought: Considering.\nAnswer: ok")
    assert agent.run_agent("hi", chat=chat, enable_reasoning=True).reasoning == "Considering."
    chat = ScriptedChat("Thought: Considering.\nAnswer: ok")
    assert agent.run_agent("hi", chat=chat, enable_reasoning=False).reasoning is None


def test_agent_package_reexports():
    import stratpoint_rag.agent as pkg

    assert hasattr(pkg, "run_agent") and hasattr(pkg, "AgentResult")
