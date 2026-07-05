from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from stratpoint_rag.agent import agent


def test_parse_link_lines():
    text = "Sources used:\n- Cloud page (https://stratpoint.com/cloud)\n- X (https://x.com/f.pdf)"
    links = agent._parse_link_lines(text)
    assert [(l.title, l.url) for l in links] == [
        ("Cloud page", "https://stratpoint.com/cloud"),
        ("X", "https://x.com/f.pdf"),
    ]


def test_build_result_captures_answer_trace_citations_resources():
    messages = [
        HumanMessage(content="cloud migration + a whitepaper?"),
        AIMessage(content="", tool_calls=[
            {"name": "search_stratpoint", "args": {"query": "cloud migration"}, "id": "c1"}
        ]),
        ToolMessage(
            content="We offer cloud migration.\n\nSources used:\n- Cloud (https://stratpoint.com/cloud)",
            name="search_stratpoint", tool_call_id="c1",
        ),
        AIMessage(content="", tool_calls=[
            {"name": "find_resource", "args": {"topic": "cloud"}, "id": "c2"}
        ]),
        ToolMessage(
            content="Downloadable resources for 'cloud':\n- AWS WP (https://aws.com/wp.pdf)",
            name="find_resource", tool_call_id="c2",
        ),
        AIMessage(content="Yes — we do cloud migration; here's a whitepaper."),
    ]
    result = agent._build_result(messages)
    assert result.answer == "Yes — we do cloud migration; here's a whitepaper."
    assert [c.url for c in result.citations] == ["https://stratpoint.com/cloud"]
    assert [r.url for r in result.resources] == ["https://aws.com/wp.pdf"]
    assert [s.type for s in result.trace] == [
        "action", "observation", "action", "observation", "answer",
    ]


def test_build_result_tool_free_answer():
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="Hello! I'm Stratpoint's assistant."),
    ]
    result = agent._build_result(messages)
    assert result.answer == "Hello! I'm Stratpoint's assistant."
    assert result.trace == [agent.Step(type="answer", content=result.answer)]
    assert result.citations == [] and result.resources == []
