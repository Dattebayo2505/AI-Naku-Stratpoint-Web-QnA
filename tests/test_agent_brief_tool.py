"""Hop-2 agent wiring: conditional registration, the manifest, id resolution.

The three failures this replaces, all of them silent:

1. the old description told the model to invent a file path;
2. nothing announced the attachment, so "what's the timeline on this?" went to
   search_stratpoint — the self-declared default tool — and answered
   confidently from the website corpus about the wrong thing;
3. the tool was offered with nothing attached, so the model called it and got
   an error Observation to reason around.
"""

import pytest

from stratpoint_rag.agent import react, tools
from stratpoint_rag.agent.contracts import ProposalPDFInput
from stratpoint_rag.docparse import BriefRef
from stratpoint_rag.docparse.schema import ExtractedRequirements


def brief(upload_id="a3f9c2", filename="client-brief.pdf", **over):
    fields = dict(
        sha256="sha-" + upload_id,
        markdown_path="/tmp/t.md",
        pages_total=12,
        pages_parsed=12,
    )
    fields.update(over)
    return BriefRef(upload_id=upload_id, filename=filename, **fields)


# ── conditional registration ────────────────────────────────────────────────


def test_the_brief_tool_is_absent_without_an_attachment():
    """A tool that cannot succeed should not be offered."""
    names = [s.name for s in tools.build_tool_specs()]

    assert tools.BRIEF_TOOL_NAME not in names


def test_the_brief_tool_appears_when_a_brief_is_attached():
    names = [s.name for s in tools.build_tool_specs([brief()])]

    assert tools.BRIEF_TOOL_NAME in names


def test_the_tool_is_not_called_parse_client_brief():
    """Tool names are part of the prompt. 'parse' implies this tool does the
    parsing, inviting the model to call it instead of prompting for an upload —
    but hop 1 already parsed the document at upload time."""
    names = [s.name for s in tools.build_tool_specs([brief()])]

    assert "parse_client_brief" not in names
    assert tools.BRIEF_TOOL_NAME == "extract_brief_requirements"


@pytest.mark.parametrize(
    "tool_name", [tools.BRIEF_TOOL_NAME, tools.READ_BRIEF_TOOL_NAME]
)
def test_the_description_asks_for_an_id_and_never_a_path(tool_name):
    spec = next(s for s in tools.build_tool_specs([brief()]) if s.name == tool_name)

    assert "upload id" in spec.description
    assert ".pdf" not in spec.description
    assert "file path" not in spec.description.lower()


def test_the_visitor_phrasings_reach_the_right_brief_tool():
    """The phrasings a visitor actually uses, so the loop knows when to reach —
    and, since there are now two brief tools, which one to reach for. These live
    in the manifest rather than on one description: "this document" is a
    question about the file under `read_brief`, and scope to be priced under
    `extract_brief_requirements`."""
    manifest = react.render_attachment_manifest([brief()])

    for phrase in ("the brief", "the RFP", "this document"):
        assert phrase in manifest
    assert tools.READ_BRIEF_TOOL_NAME in manifest
    assert tools.BRIEF_TOOL_NAME in manifest


# ── the attachment manifest ─────────────────────────────────────────────────


def test_the_manifest_carries_the_upload_id():
    """Non-negotiable: without it the id never reaches the model and the tool
    is uncallable by construction."""
    manifest = react.render_attachment_manifest([brief()])

    assert "id=a3f9c2" in manifest
    assert "client-brief.pdf" in manifest
    assert "12 pages" in manifest


def test_the_manifest_is_empty_with_no_attachments():
    assert react.render_attachment_manifest([]) == ""
    assert react.render_attachment_manifest(None) == ""


def test_the_manifest_reaches_the_system_prompt():
    prompt = react.render_system_prompt([brief()])

    assert "id=a3f9c2" in prompt
    assert tools.BRIEF_TOOL_NAME in prompt


def test_the_manifest_flags_pages_hop_one_could_not_read():
    """A brief where vision choked on 6 of 20 pages must not read like a clean
    one — the model quotes off it."""
    manifest = react.render_attachment_manifest([brief(pages_failed=[7, 8])])

    assert "unreadable" in manifest


def test_an_untranscribed_upload_says_so():
    manifest = react.render_attachment_manifest([brief(markdown_path=None)])

    assert "not transcribed yet" in manifest


def test_the_prompt_forbids_inventing_names():
    assert "Never invent a client name" in react.render_system_prompt()


def test_the_no_attachment_prompt_does_not_demand_an_upload():
    prompt = react.render_system_prompt()

    assert "No document is attached" in prompt
    assert "brief.pdf" not in prompt


# ── upload-id resolution ────────────────────────────────────────────────────


def test_the_exact_id_resolves():
    briefs = [brief("aaa"), brief("bbb", "second.pdf")]

    assert tools._resolve_upload_id("bbb", briefs).upload_id == "bbb"


@pytest.mark.parametrize("typed", ["'a3f9c2'", '"a3f9c2"', ' a3f9c2 '])
def test_quoting_and_padding_are_tolerated(typed):
    assert tools._resolve_upload_id(typed, [brief()]).upload_id == "a3f9c2"


def test_the_filename_resolves_too():
    """An 8B ReAct loop types the filename it saw in the manifest at least as
    often as the id. Nothing here touches the filesystem — the value only
    selects among uploads already resolved for this session."""
    briefs = [brief("aaa", "one.pdf"), brief("bbb", "second.pdf")]

    assert tools._resolve_upload_id("second.pdf", briefs).upload_id == "bbb"


def test_a_lone_attachment_is_assumed_when_the_id_is_wrong():
    assert tools._resolve_upload_id("brief.pdf", [brief()]).upload_id == "a3f9c2"


def test_a_wrong_id_with_several_attached_is_not_guessed():
    briefs = [brief("aaa"), brief("bbb")]

    assert tools._resolve_upload_id("ccc", briefs) is None


def test_json_input_is_accepted():
    got = tools._resolve_upload_id('{"upload_id": "a3f9c2"}', [brief()])

    assert got.upload_id == "a3f9c2"


def test_no_attachments_resolves_to_nothing():
    assert tools._resolve_upload_id("anything", []) is None


def test_the_tool_errors_clearly_when_nothing_matches():
    with pytest.raises(ValueError, match="attachment list"):
        tools.extract_brief_requirements("ccc", [brief("aaa"), brief("bbb")])


# ── the observation ─────────────────────────────────────────────────────────


def _requirements(**over):
    fields = dict(
        target_platform=["Web"],
        features=["SSO", "Checkout"],
        constraints=["12 weeks"],
        tech_stack=["Python"],
        complexity="high",
        pages_total=20,
        pages_parsed=14,
        pages_failed=[7, 8],
    )
    fields.update(over)
    return ExtractedRequirements(**fields)


def test_the_observation_reports_the_page_accounting():
    text = tools._format_requirements(_requirements())

    assert "14 of 20" in text
    assert "7, 8" in text


def test_the_observation_names_no_client_or_project():
    """The model repeats an Observation back to the visitor as established
    fact, and neither hop supplies a name."""
    text = tools._format_requirements(_requirements()).lower()

    assert "client name" not in text
    assert "project:" not in text


def test_empty_lists_read_as_absent_not_as_blank():
    text = tools._format_requirements(_requirements(constraints=[]))

    assert "Constraints: (none stated)" in text


def test_notes_are_surfaced():
    text = tools._format_requirements(_requirements(extraction_notes=["no timeline"]))

    assert "no timeline" in text


def test_the_wrapper_calls_hop_two_and_records_the_requirements(monkeypatch):
    result = _requirements()
    monkeypatch.setattr(tools, "extract_brief", lambda b: result)

    tools.begin_capture()
    try:
        text = tools._wrap_extract_brief_requirements([brief()])("a3f9c2")
        captured = tools.captured_proposal_data()
    finally:
        tools.end_capture()

    assert "SSO" in text
    assert captured.requirements is result


# ── pdf_gen with no name: the normal path, not an edge case ─────────────────


def test_the_pdf_tool_builds_a_filename_with_no_client_name(tmp_path):
    """Regression: the old code called .lower() on a required field that is now
    None whenever the visitor declines — an AttributeError at the last step of
    the proposal chain."""
    payload = ProposalPDFInput(
        requirements={}, estimation={}, output_path=str(tmp_path)
    )

    result = tools.generate_proposal_pdf(payload)

    assert result.status == "success"
    assert result.pdf_path.endswith("stratpoint_proposal_client.pdf")


def test_the_pdf_tool_accepts_none_for_both_names():
    payload = ProposalPDFInput(client_name=None, project_name=None,
                               requirements={}, estimation={})

    assert payload.client_name is None and payload.project_name is None


def test_the_pdf_tool_invents_no_client_from_a_bare_string(tmp_path):
    """'Acme Innovations' on a real quote is the same hallucination the schema
    change removed."""
    payload = f'{{"output_path": "{tmp_path.as_posix()}"}}'

    result = tools.generate_proposal_pdf(payload)

    assert "acme" not in result.pdf_path.lower()


def test_the_visitor_supplied_name_fills_the_gap(tmp_path):
    out = tmp_path / "p.pdf"
    payload = ProposalPDFInput(requirements={}, estimation={}, output_path=str(out))

    tools.generate_proposal_pdf(payload, ("Northwind Retail", "Loyalty App"))

    assert "Northwind Retail - Loyalty App" in out.read_text(encoding="utf-8")


def test_an_explicit_name_beats_the_session_one(tmp_path):
    out = tmp_path / "p.pdf"
    payload = ProposalPDFInput(
        client_name="Explicit Co", requirements={}, estimation={}, output_path=str(out)
    )

    tools.generate_proposal_pdf(payload, ("Session Co", None))

    assert "Explicit Co" in out.read_text(encoding="utf-8")


def test_the_generic_heading_when_nothing_is_known(tmp_path):
    out = tmp_path / "p.pdf"
    payload = ProposalPDFInput(requirements={}, estimation={}, output_path=str(out))

    tools.generate_proposal_pdf(payload)

    assert "Project Proposal" in out.read_text(encoding="utf-8")


# ── the loop, end to end, with no network ──────────────────────────────────


class ScriptedChat:
    def __init__(self, *turns):
        self._turns = list(turns)
        self.calls = []

    def __call__(self, messages, stop):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)


def test_the_loop_calls_the_brief_tool_with_the_id_from_the_manifest(monkeypatch):
    monkeypatch.setattr(tools, "extract_brief", lambda b: _requirements())
    chat = ScriptedChat(
        "Thought: They mean the attached brief.\n"
        "Action: extract_brief_requirements(a3f9c2)",
        "Answer: It needs SSO and checkout, on the web, in 12 weeks.",
    )

    result = react.run_react(
        "what's the timeline on this?", chat=chat, briefs=[brief()]
    )

    assert [s.type for s in result.trace] == [
        "thought", "action", "observation", "answer",
    ]
    action = next(s for s in result.trace if s.type == "action")
    assert action.tool_input == {"upload_id": "a3f9c2"}
    assert "SSO" in next(s for s in result.trace if s.type == "observation").content


def test_the_loop_reports_the_tool_as_unavailable_without_an_attachment():
    """It is not registered, so the loop answers the model with the real tool
    list rather than letting a doomed call look like a legitimate failure."""
    chat = ScriptedChat(
        "Action: extract_brief_requirements(a3f9c2)",
        "Answer: Could you upload the brief first?",
    )

    result = react.run_react("what's in the brief?", chat=chat)

    observation = next(s for s in result.trace if s.type == "observation").content
    assert "does not exist" in observation
    assert tools.BRIEF_TOOL_NAME not in observation.split("Available:")[1]


def test_the_extraction_is_captured_as_proposal_data(monkeypatch):
    requirements = _requirements()
    monkeypatch.setattr(tools, "extract_brief", lambda b: requirements)
    chat = ScriptedChat(
        "Action: extract_brief_requirements(a3f9c2)",
        "Answer: done.",
    )

    result = react.run_react("scope this", chat=chat, briefs=[brief()])

    assert result.proposal_data.requirements is requirements


def test_the_pdf_tool_in_the_loop_closes_over_the_session_names(tmp_path):
    spec = next(
        s
        for s in tools.build_tool_specs(None, ("Northwind Retail", None))
        if s.name == "generate_proposal_pdf"
    )
    out = tmp_path / "p.pdf"

    spec.fn('{"output_path": "%s"}' % out.as_posix())

    assert "Northwind Retail" in out.read_text(encoding="utf-8")
