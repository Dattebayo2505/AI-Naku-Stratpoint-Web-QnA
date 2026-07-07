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
    IntentCategory.GREETING: [],
    IntentCategory.OFF_TOPIC: [],
    IntentCategory.NEEDS_CLARIFICATION: [],
    IntentCategory.HARMFUL: [],
}

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
) -> SlotQuery:
    slot_defs = INTENT_SLOTS.get(intent, [])
    if not slot_defs:
        return SlotQuery(intent=intent, slots={}, missing_slots=[])

    slots: dict[str, str | None] = {}

    # Check project patterns first so specific names aren't consumed by topic
    for pattern, project in _PROJECT_PATTERNS:
        if pattern.search(user_input):
            slots["project_name"] = project
            break

    for pattern, topic in _TOPIC_PATTERNS:
        if pattern.search(user_input):
            slots["topic"] = topic
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

    return SlotQuery(intent=intent, slots=cleaned, missing_slots=missing)
