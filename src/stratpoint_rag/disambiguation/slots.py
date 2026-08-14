from __future__ import annotations

import logging
import re

from .schemas import IntentCategory, SlotQuery, SlotDef

log = logging.getLogger(__name__)

INTENT_SLOTS: dict[IntentCategory, list[SlotDef]] = {
    IntentCategory.ASK_STRATPOINT: [
        SlotDef(name="topic", description="What the user wants to know about", required=True),
        SlotDef(name="project_name", description="Specific Stratpoint project if mentioned", required=False),
        SlotDef(name="service_type", description="Type of service (development, consulting, etc.)", required=False),
    ],
    # NOTE the deliberately distinct names. INTENT_SLOTS[ASK_STRATPOINT] already
    # has a slot called `project_name`, and it means something entirely
    # different — *a Stratpoint case study the visitor is asking about*, not
    # *the visitor's own project*. Conflating the two would be silent and wrong.
    #
    # Both are required=False on purpose. A required slot loops until the
    # visitor supplies a value, which is exactly the coercion this whole design
    # set out to remove: declining to name the engagement is a first-class
    # answer, not a failure to answer.
    IntentCategory.REQUEST_PROPOSAL: [
        SlotDef(
            name="brief_client_name",
            description="The visitor's own client/company name for the proposal",
            required=False,
            llm_hint="Only if the visitor states it. Never infer it.",
        ),
        SlotDef(
            name="brief_project_name",
            description="The visitor's own project name for the proposal",
            required=False,
            llm_hint="Only if the visitor states it. Never infer it.",
        ),
        SlotDef(
            name="target_timeline",
            description="The visitor's target launch date or desired project duration (e.g. 3 months, Q4 2026)",
            required=False,
            llm_hint="Only if the visitor states it. Never infer it.",
        ),
    ],
    IntentCategory.GREETING: [],
    IntentCategory.OFF_TOPIC: [],
    IntentCategory.NEEDS_CLARIFICATION: [],
    IntentCategory.HARMFUL: [],
}

# An explicit "leave them blank". This is an ANSWER, not an absence of one —
# commit 7f9ae17 stopped empty input escalating to the LLM, and a blank reply
# here must be recorded as a declination rather than bounced back as ambiguous.
_DECLINE_PATTERN = re.compile(
    r"^\s*(no|nope|nah|skip|none|n/?a|blank|leave (?:it|them) blank|"
    r"no thanks?|don'?t (?:have|know)|not (?:sure|yet)|prefer not(?: to)?)"
    r"[\s.!]*$",
    re.IGNORECASE,
)

_CLIENT_ANSWER = re.compile(
    r"\bclient(?:\s*name)?\s*(?:is|:|=)\s*([^,;\n]+)", re.IGNORECASE
)
_PROJECT_ANSWER = re.compile(
    r"\bproject(?:\s*name)?\s*(?:is|:|=)\s*([^,;\n]+)", re.IGNORECASE
)


def is_declination(user_input: str) -> bool:
    """True when the visitor declined to supply a name (including by silence)."""
    text = (user_input or "").strip()
    return not text or bool(_DECLINE_PATTERN.match(text))


def _clean_name(value: str) -> str | None:
    text = value.strip().strip("\"'").strip(".,;:").strip()
    return text if 2 <= len(text) <= 80 and any(c.isalpha() for c in text) else None


def _extract_proposal_names(user_input: str, target_slot: str | None) -> dict[str, str]:
    """Pull brief_client_name / brief_project_name out of a free-text answer.

    Labelled forms win ("client is Northwind, project is Loyalty App"). An
    unlabelled answer is assigned to whichever slot was actually asked about —
    the visitor typing "Northwind Retail" at a question about the client name
    means the client name, and requiring them to repeat the label would be
    theatre.
    """
    if is_declination(user_input):
        return {}

    found: dict[str, str] = {}
    for pattern, name in (
        (_CLIENT_ANSWER, "brief_client_name"),
        (_PROJECT_ANSWER, "brief_project_name"),
    ):
        m = pattern.search(user_input)
        if m:
            cleaned = _clean_name(m.group(1))
            if cleaned:
                found[name] = cleaned

    if not found and target_slot:
        cleaned = _clean_name(user_input)
        if cleaned:
            found[target_slot] = cleaned
    return found

_TOPIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"outsystems", re.IGNORECASE), "OutSystems"),
    (re.compile(r"flutter", re.IGNORECASE), "Flutter"),
    (re.compile(r"mobile|android|ios", re.IGNORECASE), "Mobile Development"),
    (re.compile(r"web\s*(dev|app|development)", re.IGNORECASE), "Web Development"),
    (re.compile(r"cloud|aws|serverless", re.IGNORECASE), "Cloud Services"),
    (re.compile(r"ui|ux|design", re.IGNORECASE), "UI/UX Design"),
    (re.compile(r"data|analytics|ml|ai|machine.learning", re.IGNORECASE), "Data & AI"),
    (re.compile(r"devops|ci/cd|docker|kubernetes", re.IGNORECASE), "DevOps"),
    (re.compile(r"consulting|digital.transformation", re.IGNORECASE), "Consulting"),
    (re.compile(r"low.code|no.code", re.IGNORECASE), "Low-Code/No-Code"),
    (re.compile(r"retail|ecommerce|e.commerce", re.IGNORECASE), "Retail"),
    (re.compile(r"healthcare|health", re.IGNORECASE), "Healthcare"),
    (re.compile(r"finance|banking|fintech", re.IGNORECASE), "Finance"),
    (re.compile(r"pricing|cost|rate|fee|budget", re.IGNORECASE), "Pricing"),
    (re.compile(r"project|case.study|portfolio", re.IGNORECASE), "Projects"),
    (re.compile(r"career|job|hire|intern", re.IGNORECASE), "Careers"),
    (re.compile(r"service", re.IGNORECASE), "Services Overview"),
    (re.compile(r"overview|about|what\s+(?:is|are|does|do)|tell me|introduce|company", re.IGNORECASE), "General"),
    (re.compile(r"contact|email|phone|address|locat|office|reach|find", re.IGNORECASE), "Contact / Location"),
]

_SERVICE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"develop|engineering|coding|programming", re.IGNORECASE), "Development"),
    (re.compile(r"consult|advisory|strategy", re.IGNORECASE), "Consulting"),
    (re.compile(r"design|ux|ui|prototype", re.IGNORECASE), "Design"),
    (re.compile(r"manage|support|maintenance", re.IGNORECASE), "Managed Services"),
    (re.compile(r"train|workshop|upskill", re.IGNORECASE), "Training"),
    (re.compile(r"service", re.IGNORECASE), "Services"),
]

_PROJECT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sm\s*retail|sm\s*malls|retail\s*app|mall", re.IGNORECASE), "SM Retail App"),
    (re.compile(r"gcash|globe|fintech", re.IGNORECASE), "GCash/Fintech"),
    (re.compile(r"stratmega", re.IGNORECASE), "StratMega"),
    (re.compile(r"integrated\s*solutions", re.IGNORECASE), "Integrated Solutions Platform"),
]


def extract_slots(
    user_input: str,
    intent: IntentCategory,
    history: list | None = None,
    target_slot: str | None = None,
) -> SlotQuery:
    slot_defs = INTENT_SLOTS.get(intent, [])
    if not slot_defs:
        return SlotQuery(intent=intent, slots={}, missing_slots=[])

    if intent == IntentCategory.REQUEST_PROPOSAL:
        # The Stratpoint topic/service/project patterns below are about
        # Stratpoint's own catalogue and mean nothing here; running them would
        # stuff a `project_name` of "SM Retail App" into a proposal for someone
        # else's project. Both slots are optional, so missing_slots stays empty
        # and the loop terminates whatever the visitor says.
        return SlotQuery(
            intent=intent,
            slots=_extract_proposal_names(user_input, target_slot),
            missing_slots=[],
        )

    slots: dict[str, str | None] = {}
    matched_keyword: str | None = None

    # Check project patterns first so specific names aren't consumed by topic
    for pattern, project in _PROJECT_PATTERNS:
        if pattern.search(user_input):
            slots["project_name"] = project
            break

    for pattern, topic in _TOPIC_PATTERNS:
        m = pattern.search(user_input)
        if m:
            slots["topic"] = topic
            matched_keyword = m.group(0).lower()
            break

    for pattern, service in _SERVICE_PATTERNS:
        if pattern.search(user_input):
            slots["service_type"] = service
            break

    missing = [s.name for s in slot_defs if s.required and s.name not in slots]

    cleaned = {k: v for k, v in slots.items() if v is not None}

    if history:
        for hist_item in history:
            if hasattr(hist_item, "answer"):
                for pattern, topic in _TOPIC_PATTERNS:
                    if pattern.search(hist_item.answer):
                        cleaned["topic"] = topic
                        break
            elif isinstance(hist_item, dict):
                for pattern, topic in _TOPIC_PATTERNS:
                    if pattern.search(hist_item.get("answer", "")):
                        cleaned["topic"] = topic
                        break

    missing = [s for s in missing if s not in cleaned]

    return SlotQuery(intent=intent, slots=cleaned, missing_slots=missing, matched_keyword=matched_keyword)
