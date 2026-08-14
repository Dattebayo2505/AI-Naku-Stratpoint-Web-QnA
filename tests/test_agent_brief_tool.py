"""Hop-2 agent wiring: conditional registration, the manifest, id resolution.

The three failures this replaces, all of them silent:

1. the old description told the model to invent a file path;
2. nothing announced the attachment, so "what's the timeline on this?" went to
   search_stratpoint — the self-declared default tool — and answered
   confidently from the website corpus about the wrong thing;
3. the tool was offered with nothing attached, so the model called it and got
   an error Observation to reason around.
"""

import json
from pathlib import Path

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


@pytest.mark.parametrize(
    "typed",
    [
        "id=bbb",           # copied straight off the manifest line
        "upload_id=bbb",
        "id = bbb",
        "id='bbb'",
        '{"upload_id": "id=bbb"}',   # the same copy, pasted into the JSON form
    ],
)
def test_the_manifests_own_id_label_is_stripped(typed):
    """The manifest renders the id as ``| id=<value> |`` and the model copies the
    whole token — ``Action: read_brief(id=d5812cb3...)`` is a live transcript,
    not a hypothetical.

    With one brief attached this looked like it worked, because
    ``test_a_lone_attachment_is_assumed_when_the_id_is_wrong`` caught it: the
    right document came back for the wrong reason, and a bogus id would have
    reached it just as well. With two attached there is nothing to fall back to
    and the tool raises, so the loop spends a turn on an error Observation
    about a document the visitor did attach.
    """
    briefs = [brief("aaa"), brief("bbb", "second.pdf")]

    assert tools._resolve_upload_id(typed, briefs).upload_id == "bbb"


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
#
# The PDF is now a real Chromium render, so these assert against the HTML that
# is about to be printed rather than against the file's bytes: the naming rules
# are what is under test, and paying ~1s of browser launch per case to read a
# name back out of a PDF buys nothing. `test_pdf_service.py` owns the print
# stage; `test_proposal_pdf_tool.py` owns the two joined end to end.


@pytest.fixture
def rendered_html(monkeypatch):
    """Capture the HTML the tool renders, and write a stand-in PDF instead."""
    from stratpoint_rag import pdf_gen

    captured: dict[str, str] = {}

    def fake_render(html, output_path, options=None):
        captured["html"] = html
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 stand-in\n")
        return path

    monkeypatch.setattr(pdf_gen, "generate_pdf_from_html", fake_render)
    return captured


def _estimate() -> dict:
    """The minimum priced work a quote can be built from."""
    return {
        "total_cost_usd": 10_000.0,
        "estimated_weeks": 8.0,
        "role_breakdown": [
            {
                "role": "Senior Engineer",
                "estimated_hours": 100.0,
                "hourly_rate": 100.0,
                "total_cost": 10_000.0,
            }
        ],
        "summary": "8 weeks, $10,000.",
    }


def test_the_pdf_tool_builds_a_filename_with_no_client_name(tmp_path, rendered_html):
    """Regression: the old code called .lower() on a required field that is now
    None whenever the visitor declines — an AttributeError at the last step of
    the proposal chain."""
    payload = ProposalPDFInput(
        requirements={}, estimation=_estimate(), output_path=str(tmp_path)
    )

    result = tools.generate_proposal_pdf(payload)

    assert result.status == "success"
    assert result.pdf_path.endswith("stratpoint_proposal_client.pdf")


def test_the_pdf_tool_accepts_none_for_both_names():
    payload = ProposalPDFInput(client_name=None, project_name=None,
                               requirements={}, estimation={})

    assert payload.client_name is None and payload.project_name is None


def test_the_pdf_tool_invents_no_client_from_a_bare_string(tmp_path, rendered_html):
    """'Acme Innovations' on a real quote is the same hallucination the schema
    change removed."""
    payload = json.dumps({"output_path": tmp_path.as_posix(), "estimation": _estimate()})

    result = tools.generate_proposal_pdf(payload)

    assert "acme" not in result.pdf_path.lower()
    assert "acme" not in rendered_html["html"].lower()


def test_the_visitor_supplied_name_fills_the_gap(tmp_path, rendered_html):
    payload = ProposalPDFInput(
        requirements={}, estimation=_estimate(), output_path=str(tmp_path / "p.pdf")
    )

    tools.generate_proposal_pdf(payload, ("Northwind Retail", "Loyalty App"))

    assert "Northwind Retail" in rendered_html["html"]
    assert "Loyalty App" in rendered_html["html"]


def test_an_explicit_name_beats_the_session_one(tmp_path, rendered_html):
    payload = ProposalPDFInput(
        client_name="Explicit Co",
        requirements={},
        estimation=_estimate(),
        output_path=str(tmp_path / "p.pdf"),
    )

    tools.generate_proposal_pdf(payload, ("Session Co", None))

    assert "Explicit Co" in rendered_html["html"]
    assert "Session Co" not in rendered_html["html"]


def test_the_generic_heading_when_nothing_is_known(tmp_path, rendered_html):
    payload = ProposalPDFInput(
        requirements={}, estimation=_estimate(), output_path=str(tmp_path / "p.pdf")
    )

    tools.generate_proposal_pdf(payload)

    assert "Project Proposal" in rendered_html["html"]
    assert "Prospective Client" in rendered_html["html"]


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


def test_the_pdf_tool_in_the_loop_closes_over_the_session_names(tmp_path, rendered_html):
    spec = next(
        s
        for s in tools.build_tool_specs(None, ("Northwind Retail", None))
        if s.name == "generate_proposal_pdf"
    )

    spec.fn(
        json.dumps(
            {"output_path": (tmp_path / "p.pdf").as_posix(), "estimation": _estimate()}
        )
    )

    assert "Northwind Retail" in rendered_html["html"]
