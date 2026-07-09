"""Unit checks for the streaming JSON extractor (rag/answer.py).

_extract_answer_partial is a hand-rolled incremental decoder for the "answer"
field of a still-streaming GroundedAnswer JSON. These cases guard the tricky
bits: it must stay silent while `reasoning` streams first, grow monotonically as
`answer` arrives, decode escapes, and wait (not crash) on truncated escapes.
"""

from stratpoint_rag.rag.answer import _extract_answer_partial as ex


def test_none_until_answer_key_appears():
    # reasoning streams before answer in the winning variant → nothing to show yet
    assert ex('{"reasoning":"thinking about the') is None
    assert ex("{") is None
    assert ex('{"answer"') is None  # key seen but value not opened


def test_grows_monotonically_as_answer_streams():
    assert ex('{"answer":"hel') == "hel"
    assert ex('{"answer":"hello wor') == "hello wor"


def test_stops_at_closing_quote():
    assert ex('{"answer":"done","confidence":1}') == "done"


def test_decodes_escapes():
    assert ex('{"answer":"line1\\nline2"}') == "line1\nline2"
    assert ex('{"answer":"a\\ttab"}') == "a\ttab"
    assert ex('{"answer":"quote:\\" end"}') == 'quote:" end'


def test_unicode_escape():
    assert ex('{"answer":"caf\\u00e9"}') == "café"


def test_waits_on_dangling_backslash():
    # trailing "\" with no following byte yet — decode up to it, don't crash
    assert ex('{"answer":"bad\\') == "bad"


def test_waits_on_incomplete_unicode_escape():
    assert ex('{"answer":"x\\u00e') == "x"  # \u + only 3 hex digits so far


def test_ignores_answer_key_inside_reasoning_value():
    # the key mention is escaped as \"answer\" in a JSON value → must not match
    raw = '{"reasoning":"the \\"answer\\" is unclear","answer":"real"'
    assert ex(raw) == "real"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all extractor checks pass")
