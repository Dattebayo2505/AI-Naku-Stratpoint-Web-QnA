import json

from stratpoint_rag.prompts.schema import GroundedAnswer
from stratpoint_rag.prompts import few_shot_examples as fse


def test_grounded_answer_has_no_reasoning_field():
    assert "reasoning" not in GroundedAnswer.model_fields


def test_grounded_answer_constructs_without_reasoning():
    g = GroundedAnswer(answer="A", citations=[], is_grounded=True, confidence=0.9)
    assert g.answer == "A"


def test_few_shot_json_examples_have_no_reasoning_key():
    # Every JSON block embedded in the few-shot must match the trimmed schema.
    assert '"reasoning"' not in fse.FEW_SHOT_JSON_EXAMPLES


def test_few_shot_json_examples_still_parse_against_schema():
    # Each "Assistant Grounded JSON Answer" block must validate against the
    # trimmed GroundedAnswer schema (guards malformed edits).
    blocks = fse.FEW_SHOT_JSON_EXAMPLES.split("Assistant Grounded JSON Answer:")[1:]
    for block in blocks:
        start = block.index("{")
        depth = 0
        for i in range(start, len(block)):
            if block[i] == "{":
                depth += 1
            elif block[i] == "}":
                depth -= 1
                if depth == 0:
                    payload = block[start : i + 1]
                    break
        GroundedAnswer.model_validate(json.loads(payload))


def test_grounded_answer_supports_bullet_list_markdown():
    bullet_text = "We offer:\n- Service A\n- Service B"
    g = GroundedAnswer(answer=bullet_text, citations=[], is_grounded=True, confidence=1.0)
    assert g.answer == bullet_text
    serialized = g.model_dump_json()
    deserialized = GroundedAnswer.model_validate_json(serialized)
    assert deserialized.answer == bullet_text


def test_system_prompts_include_formatting_instruction():
    from stratpoint_rag.prompts.system_prompts import (
        SYSTEM_PROMPT_V0_ZEROSHOT,
        SYSTEM_PROMPT_V1_FEWSHOT,
        SYSTEM_PROMPT_V2_COT,
        SYSTEM_PROMPT_V3_ROLE_STRUCTURED,
        SYSTEM_PROMPT_V4_COMBINED,
    )
    for prompt in (
        SYSTEM_PROMPT_V0_ZEROSHOT,
        SYSTEM_PROMPT_V1_FEWSHOT,
        SYSTEM_PROMPT_V2_COT,
        SYSTEM_PROMPT_V3_ROLE_STRUCTURED,
        SYSTEM_PROMPT_V4_COMBINED,
    ):
        assert "format" in prompt.lower() or "bullet" in prompt.lower()

