"""Anchor pronoun-only questions to the company before embedding.

Visitors talk to the bot in the second person — "who are your leaders?", "do you
do cloud migration?" — but the corpus talks about Stratpoint in the third person.
bge embeds the query verbatim, so a question whose only reference to the company
is a pronoun has no entity token to match on, and the nearest neighbours come
back as generic prose (awards tables, a blog post that merely uses the word
"leaders"). Measured on the live index: "Who are all your current leaders?"
missed the About Us leadership section entirely at k=8 (best score 0.60), while
"Who are the leaders of Stratpoint?" hit it at rank 0 (0.77).

Deliberately a pure, deterministic rewrite rather than an LLM call: it runs on
every retrieval, and an extra model round-trip per query would cost more latency
than the whole retrieval step.
"""

from __future__ import annotations

import re

COMPANY = "Stratpoint"

# Second person is the visitor addressing the bot; first person plural covers
# the bot's own voice echoed back ("what are our services?"). Ordered longest
# first so 'the company' wins over any single word inside it.
_SUBJECT = ("we", "you", "the company")
_POSSESSIVE = ("your", "yours", "our", "ours")

_PRONOUNS = re.compile(
    r"\b(" + "|".join(sorted((*_SUBJECT, *_POSSESSIVE, "us"), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


# Above this many words a query is treated as pasted source text, not a
# question addressed to the bot, and is left alone. Rewriting long text is
# actively harmful: find_resource's near-verbatim lookups paste site prose that
# happens to contain "your organization"/"your infrastructure" (the WEF
# digital-maturity case in tests/test_retrieval_grounding.py), and anchoring
# those pronouns to Stratpoint broke that retrieval outright. Measured on the
# real cases: visitor questions run 4-15 words, the quote lookups 36+.
_MAX_CONVERSATIONAL_WORDS = 20


def _replacement(word: str, company: str) -> str:
    """Possessives take 's; subject/object forms are replaced bare.

    Grammar is not always perfect ("Do Stratpoint offer...") and that is fine —
    the output is only ever fed to an embedding model, never shown to the user
    or sent to the LLM as the question. What matters is that the entity token is
    present in a natural position.
    """
    return f"{company}'s" if word.lower() in _POSSESSIVE else company


def anchor_entity(query: str, company: str = COMPANY) -> str:
    """Rewrite pronoun references to `company` so the query carries the entity.

    Returns the query unchanged when it already names the company, when it is
    too long to be a conversational question, or when it contains no pronoun to
    anchor. Those guards are load-bearing: the rewrite must never touch the
    near-verbatim quote lookups `find_resource` depends on (see
    tests/test_retrieval_grounding.py). Note the pronoun guard alone is NOT
    sufficient — pasted site prose contains "your" too, which is what
    _MAX_CONVERSATIONAL_WORDS is for. Nor may an entity-less query simply have
    the company name appended: an unrelated token measurably ranks those
    lookups worse.
    """
    if not query or company.lower() in query.lower():
        return query
    if len(query.split()) > _MAX_CONVERSATIONAL_WORDS:
        return query
    return _PRONOUNS.sub(lambda m: _replacement(m.group(1), company), query)


_FORMATTING_KEYWORDS = re.compile(
    r"\b(table|tabular|chart|matrix|bullet|bullets|bulleted|csv|json|markdown|list\s*format|"
    r"format|reformat|tabulate|structure|organize|summarize|summary|simplify|shorten|expand)\b",
    re.IGNORECASE,
)

_REFERENTIAL_CUES = re.compile(
    r"\b(it|that|this|them|these|those|there|here|more|again|else)\b",
    re.IGNORECASE,
)

_CONTINUATION_PREFIX = re.compile(
    r"^(?:what\s+about|how\s+about|and\s+in|and\s+for|what\s+of|also\s+in|also\s+for)\b",
    re.IGNORECASE,
)


def is_followup_query(query: str) -> bool:
    """True when query is a follow-up, formatting request, or referential ask needing conversation context."""
    text = query.strip().lower()
    if not text:
        return False
    if bool(_FORMATTING_KEYWORDS.search(text)):
        return True
    if bool(_REFERENTIAL_CUES.search(text)):
        return True
    if bool(_CONTINUATION_PREFIX.search(text)):
        return True
    return False


def contextualize_query(
    query: str,
    history: list | None = None,
    company: str = COMPANY,
) -> str:
    """Rewrite or enrich a query with conversation context for retrieval.

    If the query is a follow-up or formatting request, enrich it with the most recent
    user question from history before applying pronoun anchoring.
    """
    if not history or not is_followup_query(query):
        return anchor_entity(query, company=company)

    last_user_query = ""
    for item in reversed(history):
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
        if role == "user" and content:
            last_user_query = content.strip()
            break

    if last_user_query and last_user_query.lower() != query.lower():
        combined = f"{last_user_query} {query}"
        return anchor_entity(combined, company=company)

    return anchor_entity(query, company=company)

