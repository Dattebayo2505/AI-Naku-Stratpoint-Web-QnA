from stratpoint_rag.prompts.builder import build_prompt
from stratpoint_rag.prompts.registry import PROMPT_VARIANTS
from stratpoint_rag.rag.models import Chunk

_CHUNKS = [Chunk(id="1", slug="s", url="https://stratpoint.com/s", title="T", text="ctx")]


def test_variant_is_registered_at_the_winning_temperature():
    v = PROMPT_VARIANTS["v4_combined_reasoning"]
    assert v.use_schema is True
    assert v.temperature == 0.1


def test_system_prompt_asks_for_reasoning_before_the_json():
    sys_p, _ = build_prompt("q", _CHUNKS, variant="v4_combined_reasoning")
    assert "Reasoning:" in sys_p
    assert "schema" in sys_p.lower()


def test_user_prompt_is_byte_identical_to_the_default_variant():
    """The documented invariant: the system prompt is the SOLE independent
    variable across variants."""
    _, user_reasoning = build_prompt("q", _CHUNKS, variant="v4_combined_reasoning")
    _, user_default = build_prompt("q", _CHUNKS, variant="v4_combined_lowtemp")
    assert user_reasoning == user_default


def test_system_prompt_differs_from_the_default_variant():
    sys_r, _ = build_prompt("q", _CHUNKS, variant="v4_combined_reasoning")
    sys_d, _ = build_prompt("q", _CHUNKS, variant="v4_combined_lowtemp")
    assert sys_r != sys_d
