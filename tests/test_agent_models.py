"""The models moved to agent/models.py to break an agent.py <-> react.py import
cycle. Both historic import paths must keep working — test_api.py imports from
the package, test_guardrail_agent.py from the agent module."""


def test_models_importable_from_package():
    from stratpoint_rag.agent import AgentResult, Link, Step  # noqa: F401


def test_models_importable_from_agent_module():
    from stratpoint_rag.agent.agent import AgentResult, Link, Step  # noqa: F401


def test_models_are_the_same_objects():
    import stratpoint_rag.agent as pkg
    from stratpoint_rag.agent import models

    assert pkg.AgentResult is models.AgentResult
    assert pkg.Link is models.Link
    assert pkg.Step is models.Step


def test_step_accepts_fallback_type():
    """The loop records a fallback as a trace step; Step.type is a free string,
    so this guards the documented vocabulary rather than a validator."""
    from stratpoint_rag.agent.models import Step

    s = Step(type="fallback", content="no Answer within 4 turns")
    assert s.type == "fallback"
    assert s.tool is None and s.tool_input is None


def test_agent_result_defaults():
    from stratpoint_rag.agent.models import AgentResult

    r = AgentResult(answer="hi")
    assert r.citations == [] and r.resources == [] and r.trace == []
    assert r.is_grounded is None and r.confidence is None
    assert r.guardrail_reason is None and r.reasoning is None
