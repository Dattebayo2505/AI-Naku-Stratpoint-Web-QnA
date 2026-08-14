"""Loop control flow, driven by an injected chat callable — no HTTP, no model."""
import pytest

from stratpoint_rag.agent import react, tools


class ScriptedChat:
    """Returns canned assistant turns in order and records what it was sent."""

    def __init__(self, *turns):
        self._turns = list(turns)
        self.calls = []

    def __call__(self, messages, stop):
        self.calls.append({"messages": [dict(m) for m in messages], "stop": list(stop)})
        if not self._turns:
            raise AssertionError("chat called more times than the script allows")
        return self._turns.pop(0)


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch):
    """Replace both tools with deterministic stand-ins.

    Patched at the source functions, not on a module-level registry: the loop
    builds its spec list per request (`build_tool_specs`), so a stale entry in
    `TOOL_REGISTRY` would no longer be what the loop dispatches.
    """
    monkeypatch.setattr(
        tools,
        "search_stratpoint",
        lambda q: f"We do {q}.\n\nSources used:\n- Cloud (https://stratpoint.com/cloud)",
    )
    monkeypatch.setattr(
        tools,
        "find_resource",
        lambda t: f"Downloadable resources for '{t}':\n- AWS WP (https://aws.com/wp.pdf)",
    )


def test_system_prompt_lists_every_tool():
    p = react.render_system_prompt()
    for spec in tools.build_tool_specs():
        assert spec.name in p
    assert "Thought:" in p and "Action:" in p and "Answer:" in p


def test_system_prompt_keeps_the_tool_narration_ban():
    p = react.render_system_prompt().lower()
    assert "never mention a tool" in p


def test_action_then_answer():
    chat = ScriptedChat(
        "Thought: Look it up.\nAction: search_stratpoint(cloud migration)",
        "Answer: Yes, we do cloud migration.",
    )
    r = react.run_react("do you do cloud migration?", chat=chat)

    assert r.answer == "Yes, we do cloud migration."
    assert [s.type for s in r.trace] == [
        "thought", "action", "observation", "answer",
    ]
    assert [c.url for c in r.citations] == ["https://stratpoint.com/cloud"]


def test_stop_sequences_are_sent():
    """Without these NIM lets llama write its own Observation and answer from
    an invented tool result."""
    chat = ScriptedChat("Answer: Hello.")
    react.run_react("hi", chat=chat)
    assert chat.calls[0]["stop"] == ["Observation:", "PAUSE"]


def test_observation_is_fed_back_as_a_user_message():
    chat = ScriptedChat(
        "Action: search_stratpoint(cloud)",
        "Answer: Done.",
    )
    react.run_react("cloud?", chat=chat)
    second = chat.calls[1]["messages"][-1]
    assert second["role"] == "user"
    assert second["content"].startswith("Observation: We do cloud.")


def test_find_resource_populates_resources_not_citations():
    chat = ScriptedChat(
        "Action: find_resource(cloud whitepaper)",
        "Answer: Here it is: [AWS WP](https://aws.com/wp.pdf)",
    )
    r = react.run_react("got a whitepaper?", chat=chat)
    assert [x.url for x in r.resources] == ["https://aws.com/wp.pdf"]
    assert r.citations == []


def test_history_is_threaded_after_the_system_prompt():
    chat = ScriptedChat("Answer: Sure.")
    react.run_react(
        "and pricing?",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        chat=chat,
    )
    sent = chat.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "and pricing?"},
    ]


def test_unknown_tool_is_recoverable_and_does_not_spend_the_reprompt():
    chat = ScriptedChat(
        "Action: lookup_everything(cloud)",
        "Action: search_stratpoint(cloud)",
        "Answer: We do cloud.",
    )
    r = react.run_react("cloud?", chat=chat)
    assert r.answer == "We do cloud."
    obs = [s for s in r.trace if s.type == "observation"]
    assert "does not exist" in obs[0].content
    assert "search_stratpoint" in obs[0].content  # lists what IS available


def test_one_reprompt_then_success():
    chat = ScriptedChat(
        "The search_stratpoint function returns page text.",
        "Answer: We do cloud.",
    )
    r = react.run_react("cloud?", chat=chat)
    assert r.answer == "We do cloud."
    corrective = chat.calls[1]["messages"][-1]
    assert corrective["role"] == "user"
    assert "valid Action or Answer" in corrective["content"]
    assert not any(s.type == "fallback" for s in r.trace)


def test_second_malformed_turn_falls_back_to_rag():
    chat = ScriptedChat("Just prose.", "Still prose.")
    r = react.run_react("what do you do?", chat=chat)

    assert r.answer.startswith("We do what do you do?.")
    fb = [s for s in r.trace if s.type == "fallback"]
    assert len(fb) == 1 and "reprompt" in fb[0].content
    assert [c.url for c in r.citations] == ["https://stratpoint.com/cloud"]


def test_turn_limit_falls_back_to_rag():
    """Four actions with no Answer — the loop must not spin forever."""
    chat = ScriptedChat(*["Action: search_stratpoint(x)"] * react.MAX_TURNS)
    r = react.run_react("hello?", chat=chat)

    fb = [s for s in r.trace if s.type == "fallback"]
    assert len(fb) == 1 and str(react.MAX_TURNS) in fb[0].content
    assert r.answer.startswith("We do hello?.")


def test_fallback_calls_the_tool_so_guardrails_have_chunks(monkeypatch):
    """The fallback must run search_stratpoint rather than return a canned
    message — the output guardrails verify against the chunks it records, and
    an answer with no sources gets blocked as unverifiable."""
    calls = []
    monkeypatch.setattr(
        tools, "search_stratpoint", lambda q: calls.append(q) or "Grounded."
    )
    chat = ScriptedChat("prose", "prose")
    react.run_react("the original question", chat=chat)
    assert calls == ["the original question"]


def test_reasoning_surfaces_thoughts_when_enabled():
    chat = ScriptedChat(
        "Thought: First I search.\nAction: search_stratpoint(cloud)",
        "Thought: Now I can answer.\nAnswer: We do cloud.",
    )
    r = react.run_react("cloud?", chat=chat, enable_reasoning=True)
    assert r.reasoning == "First I search.\n\nNow I can answer."


def test_reasoning_is_none_when_disabled_but_thoughts_stay_in_the_trace():
    """The flag controls SURFACING, not generation: ReAct needs the Thought to
    route correctly, so it cannot be switched off."""
    chat = ScriptedChat("Thought: Thinking.\nAnswer: Done.")
    r = react.run_react("q", chat=chat, enable_reasoning=False)
    assert r.reasoning is None
    assert [s.content for s in r.trace if s.type == "thought"] == ["Thinking."]


def test_tool_input_is_labelled_with_the_spec_arg_name():
    chat = ScriptedChat("Action: find_resource(cloud)", "Answer: ok")
    r = react.run_react("q", chat=chat)
    action = next(s for s in r.trace if s.type == "action")
    assert action.tool_input == {"topic": "cloud"}


# ── the real chat payload ───────────────────────────────────────────────────
#
# Every test above injects `chat`, which is what made the defect below invisible
# offline: the only caller that passes no stop sequences is the document
# fallback, and it failed on every real invocation. `httpx.post` is captured
# rather than called, so this file's "no HTTP" promise still holds.


@pytest.fixture
def payloads(monkeypatch):
    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(react.config, "nvidia_api_key", lambda: "k")
    monkeypatch.setattr(
        react.httpx, "post", lambda *a, **kw: sent.append(kw["json"]) or _Resp()
    )
    return sent


def test_an_empty_stop_list_is_omitted_not_sent_as_an_empty_array(payloads):
    """NIM answers `400 Validation: Stop sequences array cannot be empty`.

    `_brief_fallback` is the only caller that passes none — the safety net that
    answers from the visitor's own document when the loop stalls — so it raised
    every time and degraded to its apology string, which reads exactly like the
    model having nothing to say. Measured live before the fix.
    """
    react._default_chat([{"role": "user", "content": "hi"}], [])

    assert "stop" not in payloads[0]


def test_real_stop_sequences_are_still_sent(payloads):
    """Omitting the empty list must not drop the loop's actual stop sequences —
    without them the model writes its own Observation and answers from it."""
    react._default_chat([{"role": "user", "content": "hi"}], react.STOP)

    assert payloads[0]["stop"] == react.STOP
