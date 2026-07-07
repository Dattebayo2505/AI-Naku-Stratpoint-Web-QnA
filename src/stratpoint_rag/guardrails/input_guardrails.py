from __future__ import annotations

import json
import logging
import re

import httpx

from stratpoint_rag.rag import config

from .schemas import GuardrailResult, RedactionRule

log = logging.getLogger(__name__)


class PIIRedactor:
    DEFAULT_RULES: list[RedactionRule] = [
        RedactionRule(pattern=r"\b\d{3}-\d{2}-\d{4}\b", replacement="[SSN]", entity_type="ssn"),
        RedactionRule(
            pattern=r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
            replacement="[CREDIT_CARD]",
            entity_type="credit_card",
        ),
        RedactionRule(pattern=r"[\w.+-]+@[\w-]+\.[\w.-]+", replacement="[EMAIL]", entity_type="email"),
        RedactionRule(
            pattern=r"\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}",
            replacement="[PHONE]",
            entity_type="phone",
        ),
    ]

    def __init__(self, rules: list[RedactionRule] | None = None):
        self.rules = rules or list(self.DEFAULT_RULES)
        self._compiled = [(re.compile(r.pattern), r) for r in self.rules]

    def redact(self, text: str) -> tuple[str, list[RedactionRule]]:
        modified = text
        matched_rules: list[RedactionRule] = []
        for compiled, rule in self._compiled:
            if compiled.search(modified):
                matched_rules.append(rule)
                modified = compiled.sub(rule.replacement, modified)
        return modified, matched_rules


STRATPOINT_KEYWORDS = {
    "stratpoint", "outsystems", "flutter", "mobile", "web", "app",
    "software", "consulting", "project", "service", "technology",
    "development", "cloud", "aws", "design", "ux", "ui", "digital",
    "retail", "healthcare", "finance", "startup", "enterprise",
    "low-code", "no-code", "api", "microservice", "serverless",
    "react", "angular", "vue", "python", "javascript", "typescript",
    "database", "devops", "ci/cd", "agile", "scrum", "qa", "testing",
    "docker", "kubernetes", "ml", "ai", "data", "analytics",
}


class TopicFilter:
    def __init__(self, use_llm_fallback: bool = True):
        self.use_llm_fallback = use_llm_fallback

    def check(self, text: str) -> GuardrailResult:
        text_lower = text.lower()
        matches = [kw for kw in STRATPOINT_KEYWORDS if kw in text_lower]

        if matches:
            return GuardrailResult(
                passed=True,
                action="allow",
                message=f"Matched Stratpoint keywords: {matches[:5]}",
            )

        if not self.use_llm_fallback:
            return GuardrailResult(
                passed=True,
                action="allow",
                message="No keywords found; LLM fallback disabled — allowing",
            )

        return self._llm_check(text)

    def _llm_check(self, text: str) -> GuardrailResult:
        key = config.nvidia_api_key()
        if not key:
            return GuardrailResult(passed=True, action="allow", message="No API key — allowing")

        try:
            resp = httpx.post(
                f"{config.nvidia_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.llm_model(),
                    "messages": [
                        {
                            "role": "system",
                            "content": "You check if user input relates to Stratpoint, software dev, or tech consulting. JSON only.",
                        },
                        {
                            "role": "user",
                            "content": (
                                f'Answer JSON: {{"is_related": true/false, "reasoning": "..."}}\nUser: {text}'
                            ),
                        },
                    ],
                    "max_tokens": 128,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            if data.get("is_related", True):
                return GuardrailResult(passed=True, action="allow", message="LLM confirmed related")
            return GuardrailResult(passed=True, action="allow", message="LLM: unrelated but allowing (advisory)")
        except Exception as e:
            log.warning("LLM topic check failed: %s", e)
            return GuardrailResult(passed=True, action="allow", message=f"Topic check error — allowing")


BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(?:all\s+)?(?:previous|above|below)\s+(?:instructions|prompts?|messages?)", re.IGNORECASE), "prompt_injection"),
    (re.compile(r"(system|default)\s+prompt", re.IGNORECASE), "system_prompt_request"),
    (re.compile(r"\byou are now\b", re.IGNORECASE), "role_override"),
    (re.compile(r"\bDAN\b"), "jailbreak_attempt"),
    (re.compile(r"\bjailbreak\b", re.IGNORECASE), "jailbreak_attempt"),
    (re.compile(r"\bbypass\s+(rules?|filter|restrictions?|security)", re.IGNORECASE), "bypass_attempt"),
    (re.compile(r"(reveal|show|give|output|print|display|leak)\s+(the )?(secret|hidden|internal|private)", re.IGNORECASE), "info_leak_request"),
    (re.compile(r"how\s+(to\s+)?(hack|crack|exploit|scam)", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bhack\b", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bexploit\b", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bmalware\b", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bransomware\b", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bcrack\s+(software|password|account|system|code)", re.IGNORECASE), "harmful_request"),
    (re.compile(r"\bDDoS\b", re.IGNORECASE), "harmful_request"),
    (re.compile(r"(SQL|NoSQL|XSS|CSRF)\s+injection", re.IGNORECASE), "attack_pattern"),
]


class KeywordBlocker:
    def __init__(self, patterns: list[tuple[re.Pattern, str]] | None = None):
        self._patterns = patterns or BLOCKED_PATTERNS

    def check(self, text: str) -> GuardrailResult:
        for compiled, category in self._patterns:
            if compiled.search(text):
                return GuardrailResult(
                    passed=False, action="block", message=f"Blocked: matched '{category}'"
                )
        return GuardrailResult(passed=True, action="allow", message="No blocked keywords")


class InputPipeline:
    def __init__(
        self,
        redactor: PIIRedactor | None = None,
        topic_filter: TopicFilter | None = None,
        keyword_blocker: KeywordBlocker | None = None,
    ):
        self.redactor = redactor or PIIRedactor()
        self.topic_filter = topic_filter or TopicFilter()
        self.keyword_blocker = keyword_blocker or KeywordBlocker()

    def run(self, user_input: str) -> tuple[str, list[GuardrailResult]]:
        results: list[GuardrailResult] = []

        keyword_result = self.keyword_blocker.check(user_input)
        results.append(keyword_result)
        if not keyword_result.passed:
            return user_input, results

        redacted, matched_rules = self.redactor.redact(user_input)
        if matched_rules:
            results.append(GuardrailResult(
                passed=True,
                action="redact",
                message=f"Redacted {len(matched_rules)} entities: {[r.entity_type for r in matched_rules]}",
                modified_input=redacted,
            ))
        else:
            results.append(GuardrailResult(passed=True, action="allow", message="No PII detected"))

        topic_result = self.topic_filter.check(redacted)
        results.append(topic_result)

        return redacted, results
