from __future__ import annotations

import json
import logging

import httpx

from stratpoint_rag.rag import config

from .schemas import (
    ClarificationTurn,
    IntentCategory,
    SlotDef,
    SlotQuery,
)

log = logging.getLogger(__name__)

INTENT_SLOTS: dict[IntentCategory, list[SlotDef]] = {
    IntentCategory.ASK_STRATPOINT: [
        SlotDef(
            name="topic",
            description="What the user wants to know about (e.g. OutSystems, Flutter, mobile dev, cloud, pricing, projects)",
            required=True,
            llm_hint="Extract the main subject or question topic from the user's input",
        ),
        SlotDef(
            name="project_name",
            description="Specific Stratpoint project name if mentioned",
            required=False,
            llm_hint="If the user references a specific project by name, capture it here",
        ),
        SlotDef(
            name="service_type",
            description="Type of service asked about (development, consulting, design, etc.)",
            required=False,
            llm_hint="Extract the service category if mentioned",
        ),
    ],
    IntentCategory.GREETING: [],
    IntentCategory.OFF_TOPIC: [],
    IntentCategory.NEEDS_CLARIFICATION: [],
    IntentCategory.HARMFUL: [],
}

SLOT_EXTRACTION_PROMPT = """You are a slot extractor for a Stratpoint customer-support chatbot.

Given the user's input and their identified intent, extract the relevant slot values.

Intent: {intent}

Slot definitions:
{slot_definitions}

User input: {user_input}

Conversation history (clarification turns so far):
{history}

Respond with a JSON object:
```json
{{
  "slots": {{
    "topic": "extracted value or null",
    "project_name": "extracted value or null",
    "service_type": "extracted value or null"
  }},
  "missing_slots": ["list of slot names that could not be filled"]
}}
```

Only include a slot in missing_slots if it is required and could not be extracted from the input or conversation history."""


def _call_llm(prompt: str) -> dict:
    key = config.nvidia_api_key()
    if not key:
        msg = "NVIDIA_API_KEY is not set (see .envexample)"
        raise RuntimeError(msg)

    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": config.llm_model(),
            "messages": [
                {"role": "system", "content": "You extract slot values from user input. Respond with JSON only."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 512,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    return json.loads(raw)


def extract_slots(
    user_input: str,
    intent: IntentCategory,
    history: list[ClarificationTurn] | None = None,
) -> SlotQuery:
    slot_defs = INTENT_SLOTS.get(intent, [])
    if not slot_defs:
        return SlotQuery(intent=intent, slots={}, missing_slots=[])

    slot_defs_text = "\n".join(
        f"- {s.name} ({'required' if s.required else 'optional'}): {s.description}"
        for s in slot_defs
    )

    history_text = "No prior clarification turns."
    if history:
        history_lines = [f"Q: {t.question}\nA: {t.answer}" for t in history]
        history_text = "\n".join(history_lines)

    prompt = SLOT_EXTRACTION_PROMPT.format(
        intent=intent.value,
        slot_definitions=slot_defs_text,
        user_input=user_input,
        history=history_text,
    )

    try:
        data = _call_llm(prompt)
        slots = data.get("slots", {})
        missing = data.get("missing_slots", [])

        cleaned_slots = {k: v for k, v in slots.items() if v is not None}

        required_names = {s.name for s in slot_defs if s.required}
        missing_from_required = [s for s in missing if s in required_names]

        return SlotQuery(
            intent=intent,
            slots=cleaned_slots,
            missing_slots=missing_from_required,
        )
    except Exception as e:
        log.warning("Slot extraction failed: %s", e)
        return SlotQuery(
            intent=intent,
            slots={},
            missing_slots=[s.name for s in slot_defs if s.required],
        )
