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
            # Require ≥7 digits so percentages (99.99%), version/dotted numbers
            # (5.5, 1.2.3.4) and dates (2020/09/30) are not mistaken for phones.
            # ponytail: digit-count heuristic, not a real phone parser — misses
            # exotic groupings like "(02) 8123-4567"; swap for `phonenumbers` if
            # that ever matters.
            pattern=r"\+?\d(?:[\s.\-()]?\d){6,}",
            replacement="[PHONE]",
            entity_type="phone",
        ),
    ]

    def __init__(
        self,
        rules: list[RedactionRule] | None = None,
        allowed_email_domains: set[str] | None = None,
    ):
        self.rules = rules or list(self.DEFAULT_RULES)
        self._allowed_email_domains = allowed_email_domains or set()
        self._compiled = [(re.compile(r.pattern), r) for r in self.rules]

    def redact(self, text: str) -> tuple[str, list[RedactionRule]]:
        modified = text
        matched_rules: list[RedactionRule] = []
        for compiled, rule in self._compiled:
            if not compiled.search(modified):
                continue
            if rule.entity_type == "email" and self._allowed_email_domains:
                redacted_count = 0

                def _replacer(m: re.Match) -> str:
                    nonlocal redacted_count
                    domain = m.group(0).split("@")[-1]
                    if domain.lower() in self._allowed_email_domains:
                        return m.group(0)
                    redacted_count += 1
                    return rule.replacement

                modified = compiled.sub(_replacer, modified)
                if redacted_count > 0:
                    matched_rules.append(rule)
            else:
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
    "offer", "offers", "offering", "offerings", "capability", "capabilities",
    "solution", "solutions", "feature", "features", "strength", "strengths",
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
                timeout=config.llm_timeout(),
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


IRRELEVANT_DOC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(curriculum\s+vitae|resume|work\s+experience|employment\s+history|"
            r"education\s+history|professional\s+experience|skills\s+&\s+proficiencies|"
            r"career\s+objective|personal\s+profile|references\s+available)\b",
            re.IGNORECASE,
        ),
        "resume",
    ),
    (
        re.compile(
            r"\b(reflection\s+paper|essay\s+title|essay\s+prompt|course\s+code|"
            r"professor\s*[:\-]|instructor\s*[:\-]|student\s+id|thesis\s+statement|"
            r"term\s+paper|rubric\b)",
            re.IGNORECASE,
        ),
        "academic_essay",
    ),
    (
        re.compile(
            r"\b(problem\s+set|homework\s*#?\d+|assignment\s*#?\d+|solve\s+for\s+[a-z]|"
            r"show\s+(?:your\s+)?work|exercises\s*#?\d+|find\s+the\s+derivative|"
            r"calculate\s+the\s+integral|differential\s+equation)\b",
            re.IGNORECASE,
        ),
        "homework_assignment",
    ),
]

_POSITIVE_RFP_PATTERNS = re.compile(
    r"\b(scope\s+of\s+work|deliverables|functional\s+requirements|system\s+architecture|"
    r"project\s+timeline|terms\s+of\s+reference|request\s+for\s+proposal|\brfp\b|"
    r"client\s+brief|statement\s+of\s+work|\bsow\b|user\s+stories|technical\s+requirements|"
    r"client\s*:\s*\w+|project\s*:\s*\w+)\b",
    re.IGNORECASE,
)


_RESUME_BIO_TOKENS = re.compile(
    r"\b(gpa\b|bachelor\s+of|master\s+of|dean's\s+list|graduated\s+(?:in|with)|cum\s+laude)\b",
    re.IGNORECASE,
)


class DocumentRelevanceFilter:
    """Evaluates whether an uploaded document transcription is a valid project brief/RFP
    or an irrelevant document (resume, academic essay, math problem set, reflection paper)."""

    def __init__(self, use_llm_fallback: bool = True):
        self.use_llm_fallback = use_llm_fallback

    def check(self, text: str) -> GuardrailResult:
        if not text or not text.strip():
            return GuardrailResult(passed=True, action="allow", message="Empty document allowed")

        sample = text[:4000]

        # 1. Positive RFP bypass: if the document clearly identifies as an RFP / project spec
        if _POSITIVE_RFP_PATTERNS.search(sample):
            # Only bypass if it doesn't also look overwhelmingly like a single student essay or resume
            if not (_RESUME_BIO_TOKENS.search(sample) and "resume" in sample.lower()):
                return GuardrailResult(
                    passed=True,
                    action="allow",
                    message="Document contains clear project RFP/brief markers",
                )

        # 2. Heuristic check for known irrelevant categories
        for pattern, doc_type in IRRELEVANT_DOC_PATTERNS:
            if pattern.search(sample):
                if doc_type == "resume":
                    # Extra check for resume bio tokens or education/experience combination
                    if _RESUME_BIO_TOKENS.search(sample) or ("education" in sample.lower() and "experience" in sample.lower()):
                        return GuardrailResult(
                            passed=False,
                            action="block",
                            message=f"Irrelevant document detected: {doc_type}",
                        )
                else:
                    return GuardrailResult(
                        passed=False,
                        action="block",
                        message=f"Irrelevant document detected: {doc_type}",
                    )

        # 3. LLM Fallback if heuristics are inconclusive
        if not self.use_llm_fallback:
            return GuardrailResult(passed=True, action="allow", message="Heuristics passed; LLM fallback disabled")

        return self._llm_check(sample[:2000])

    def _llm_check(self, text_sample: str) -> GuardrailResult:
        key = config.nvidia_api_key()
        if not key:
            return GuardrailResult(passed=True, action="allow", message="No API key — allowing document")

        try:
            resp = httpx.post(
                f"{config.nvidia_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.llm_model(),
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an input document validator for a software engineering and cloud consulting chatbot.\n"
                                "Determine if the document is a software project brief, RFP, technical requirements specification, or commercial consultation document.\n"
                                "If the document is a resume/CV, homework/math problem set, student essay, reflection paper, or personal non-project file, set is_project_brief to false.\n"
                                "Respond strictly with JSON: {\"is_project_brief\": bool, \"document_type\": str, \"reason\": str}"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Document excerpt:\n{text_sample}",
                        },
                    ],
                    "max_tokens": 128,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=config.llm_timeout(),
            )
            resp.raise_for_status()
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            if data.get("is_project_brief", True):
                return GuardrailResult(passed=True, action="allow", message="LLM confirmed project brief")
            doc_type = data.get("document_type", "unrelated_document")
            return GuardrailResult(
                passed=False,
                action="block",
                message=f"Irrelevant document detected: {doc_type}",
            )
        except Exception as e:
            log.warning("LLM document relevance check failed: %s", e)
            return GuardrailResult(passed=True, action="allow", message="Document check error — allowing")

