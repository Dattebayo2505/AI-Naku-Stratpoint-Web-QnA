"""Parser for plain-text ReAct output.

Note on PAUSE: the loop sends stop=["Observation:", "PAUSE"], so the model's
text is cut BEFORE those tokens are emitted. The parser must therefore never
require a literal PAUSE — it keys on 'Action:'.
"""
from stratpoint_rag.agent.react import parse_step


def test_thought_and_action_unquoted_arg():
    p = parse_step("Thought: I should look this up.\nAction: search_stratpoint(cloud migration)")
    assert p.kind == "action"
    assert p.thoughts == ["I should look this up."]
    assert p.tool == "search_stratpoint"
    assert p.tool_input == "cloud migration"


def test_double_quoted_arg_is_unquoted():
    p = parse_step('Action: find_resource("cloud whitepaper")')
    assert p.tool_input == "cloud whitepaper"


def test_single_quoted_arg_is_unquoted():
    p = parse_step("Action: find_resource('cloud whitepaper')")
    assert p.tool_input == "cloud whitepaper"


def test_arg_containing_parentheses_keeps_them():
    """Greedy-to-last-paren on the action's own line. A non-greedy match would
    truncate here, and find_resource depends on the visitor's exact wording."""
    p = parse_step("Action: find_resource(automation by 2027 (Gartner))")
    assert p.tool_input == "automation by 2027 (Gartner)"


def test_answer_only():
    p = parse_step("Answer: We offer cloud migration services.")
    assert p.kind == "answer"
    assert p.answer == "We offer cloud migration services."
    assert p.tool is None


def test_thought_then_answer():
    p = parse_step("Thought: I have enough now.\nAnswer: We do cloud.")
    assert p.kind == "answer"
    assert p.thoughts == ["I have enough now."]
    assert p.answer == "We do cloud."


def test_multiline_answer_is_kept_whole():
    p = parse_step("Answer: Line one.\nLine two.\n- A doc (https://x.com/f.pdf)")
    assert p.answer == "Line one.\nLine two.\n- A doc (https://x.com/f.pdf)"


def test_action_before_answer_wins():
    """A common 8B failure: appending a speculative answer after an action it
    has not run yet. The action must win, or we answer from nothing."""
    p = parse_step("Action: search_stratpoint(ceo)\nAnswer: I think it is someone.")
    assert p.kind == "action"
    assert p.tool == "search_stratpoint"


def test_answer_before_action_wins():
    p = parse_step("Answer: We do cloud.\nAction: search_stratpoint(more)")
    assert p.kind == "answer"
    assert p.answer.startswith("We do cloud.")


def test_multiple_thoughts_all_collected():
    p = parse_step("Thought: First.\nThought: Second.\nAnswer: Done.")
    assert p.thoughts == ["First.", "Second."]


def test_prose_is_malformed():
    p = parse_step("The find_resource function returns a list of PDFs.")
    assert p.kind == "malformed"
    assert p.tool is None and p.answer is None


def test_action_without_closing_paren_is_malformed():
    p = parse_step("Action: search_stratpoint(cloud migration")
    assert p.kind == "malformed"


def test_empty_answer_is_malformed():
    """An empty Answer is useless to the visitor — treat it as a parse failure
    so the loop reprompts instead of returning a blank turn."""
    p = parse_step("Answer:   ")
    assert p.kind == "malformed"


def test_empty_text_is_malformed():
    assert parse_step("").kind == "malformed"
