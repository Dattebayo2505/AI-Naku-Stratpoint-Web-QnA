"""Regression tests for entity-less ("Who are all your current leaders?") queries.

The bug: `retrieve()` embedded the visitor's words verbatim. A question that
refers to Stratpoint only by pronoun — "your leaders", "who runs the company" —
carries no entity token, so bge's nearest neighbours were generic prose about
leadership (awards pages, a blog post on women in IT) rather than the About Us
leadership section. Retrieval never surfaced the answer, so the LLM answered
from junk context while still reporting is_grounded=True.

The same question WITH the entity ("Who are the leaders of Stratpoint?")
retrieved the right chunk at rank 0, which is what made the pair look like a
prompting bug rather than a retrieval one.

The unit tests below cover the pure rewrite; the integration test pins the
end-to-end retrieval behaviour and needs the built Chroma store.
"""
import pytest

from stratpoint_rag.rag.query_rewrite import anchor_entity, contextualize_query, is_followup_query


class TestAnchorEntity:
    """Pure-function tests: no store, no embedder, no network."""

    @pytest.mark.parametrize(
        "query, expected",
        [
            ("Who are all your current leaders?", "Who are all Stratpoint's current leaders?"),
            ("Who runs the company?", "Who runs Stratpoint?"),
            ("Who is your CEO?", "Who is Stratpoint's CEO?"),
            ("Do you offer cloud migration?", "Do Stratpoint offer cloud migration?"),
            ("Where are your offices?", "Where are Stratpoint's offices?"),
            ("What are our core services?", "What are Stratpoint's core services?"),
        ],
    )
    def test_pronouns_are_anchored_to_the_company(self, query, expected):
        assert anchor_entity(query) == expected

    @pytest.mark.parametrize(
        "query",
        [
            "Who are the leaders of Stratpoint?",
            "Does stratpoint offer cloud migration?",  # case-insensitive detection
            "What is Stratpoint's approach to your data?",  # entity already present
        ],
    )
    def test_query_naming_the_company_is_left_untouched(self, query):
        assert anchor_entity(query) == query

    @pytest.mark.parametrize(
        "query",
        [
            "mobile phone usage patterns",
            "A Nucleus Research study shows Cloud adoption",
            "how many business tasks will be automated by 2027",
        ],
    )
    def test_entityless_query_without_pronouns_is_left_untouched(self, query):
        """No blind appending: near-verbatim quote lookups (find_resource's whole
        job, see tests/test_retrieval_grounding.py) must keep their exact wording.
        Only a pronoun gives us licence to rewrite."""
        assert anchor_entity(query) == query

    def test_long_pasted_prose_is_left_untouched(self):
        """The WEF digital-maturity lookup in tests/test_retrieval_grounding.py.

        Caught as a real regression: pasted site prose contains "your" too, so
        the pronoun guard alone rewrote it to "Stratpoint's organization" and
        broke that retrieval. Length is what separates a question to the bot
        from source text quoted back at it.
        """
        quote = (
            "A robust and flexible infrastructure ensures that your organization can "
            "quickly respond to market changes and scale operations as needed. Can your "
            "infrastructure handle increasing data volumes and support future growth?"
        )
        assert anchor_entity(quote) == quote

    def test_word_boundaries_are_respected(self):
        """'us' inside 'Australia'/'focus' must not be rewritten."""
        assert anchor_entity("Do you have focus in Australia?") == (
            "Do Stratpoint have focus in Australia?"
        )

    def test_is_followup_query_detects_formatting_and_pronouns(self):
        assert is_followup_query("Output me in table format") is True
        assert is_followup_query("Put this in a table") is True
        assert is_followup_query("Summarize in bullet points") is True
        assert is_followup_query("Tell me more about it") is True
        assert is_followup_query("What services does Stratpoint offer?") is False

    def test_contextualize_query_enriches_followup_with_history(self):
        history = [
            {"role": "user", "content": "List all job openings for Stratpoint"},
            {"role": "assistant", "content": "We have Lead Designer and Delivery Manager..."},
        ]
        result = contextualize_query("Output me in table format", history=history)
        assert "List all job openings for Stratpoint" in result
        assert "table format" in result

    def test_contextualize_query_leaves_standalone_query_intact(self):
        history = [
            {"role": "user", "content": "List all job openings for Stratpoint"},
            {"role": "assistant", "content": "We have Lead Designer..."},
        ]
        result = contextualize_query("What is OutSystems?", history=history)
        assert result == "What is OutSystems?"


# The chunk that answers the question: the About Us leadership section.
_LEADERSHIP_MARKERS = ("Executive Chairman", "Chief Executive Officer")


@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    [
        "Who are all your current leaders?",
        "Who runs the company?",
    ],
)
def test_pronoun_query_retrieves_the_leadership_section(query):
    """End-to-end at the retrieve() seam — the real bug pattern.

    Requires the built Chroma store (`uv run stratpoint-rag-ingest`) and the
    local embedding model, so it is deselected from the default unit run.
    """
    from stratpoint_rag.rag.retrieve import retrieve

    chunks = retrieve(query, k=8)
    assert any(
        all(m in c.text for m in _LEADERSHIP_MARKERS) for c in chunks
    ), f"leadership section absent from top-8 for {query!r}: {[c.slug for c in chunks]}"
