from __future__ import annotations

import json
import logging
import re

import httpx

from stratpoint_rag.rag import config
from stratpoint_rag.rag.embeddings import get_embedder
from stratpoint_rag.rag.models import Chunk

from .input_guardrails import PIIRedactor
from .schemas import GuardrailResult

log = logging.getLogger(__name__)

ADVICE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Professional — requires directive 2nd-person structure (already safe)
    (re.compile(r"(I|you)\s+(should|ought to|need to|must|had better)\s+(see|consult|contact|ask)\s+a\s+(doctor|lawyer|attorney|therapist|financial advisor|accountant)", re.IGNORECASE), "professional_advice"),
    # Medical — only flag when telling the user to take personal medical action
    (re.compile(r"you\s+(should|need to|must|ought to|had better)\s+(see|consult|visit)\s+a\s+(doctor|physician|specialist|therapist)", re.IGNORECASE), "medical_advice"),
    (re.compile(r"you\s+(should|need to|must)\s+get\s+(a\s+)?(diagnosis|prescription|treatment)", re.IGNORECASE), "medical_advice"),
    # Legal — only flag when telling the user to contact a lawyer
    (re.compile(r"you\s+(should|need to|must)\s+(contact|consult)\s+(a\s+)?(lawyer|attorney)", re.IGNORECASE), "legal_advice"),
    # Financial — only flag specific stock/market tips, not broad "invest in"
    (re.compile(r"(stock\s+pick|buy\s+shares|market\s+tip|trading\s+advice)", re.IGNORECASE), "financial_advice"),
    (re.compile(r"you\s+(should|need to|must)\s+invest\s+in\s+(this|that|the\s+following|a\s+)", re.IGNORECASE), "financial_advice"),
]


class OutputPIIChecker:
    ALLOWED_BUSINESS_DOMAINS = {"stratpoint.com"}

    def __init__(self):
        self.redactor = PIIRedactor(allowed_email_domains=self.ALLOWED_BUSINESS_DOMAINS)

    def check(self, response: str, source_chunks: list[Chunk]) -> GuardrailResult:
        source_text = " ".join(c.text for c in source_chunks)
        redacted, matched_rules = self.redactor.redact(response)

        if not matched_rules:
            return GuardrailResult(passed=True, action="allow", message="No PII in output")

        source_leaked = []
        output_only = []
        for rule in matched_rules:
            if re.search(rule.pattern, source_text):
                source_leaked.append(rule.entity_type)
            else:
                output_only.append(rule.entity_type)

        if output_only:
            return GuardrailResult(
                passed=False,
                action="redact",
                message=f"PII leaked in output (not in source): {output_only}",
                modified_output=redacted,
            )

        return GuardrailResult(
            passed=True,
            action="allow",
            message=f"PII in output but present in source docs: {source_leaked}",
            modified_output=redacted,
        )


class HallucinationChecker:
    def __init__(self, similarity_threshold: float = 0.6):
        self.threshold = similarity_threshold
        self._embedder = None

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def check(self, response: str, source_chunks: list[Chunk], use_llm: bool = False) -> GuardrailResult:
        if not source_chunks:
            return GuardrailResult(
                passed=False,
                action="escalate",
                message="No source chunks to verify against",
            )

        try:
            embedder = self._get_embedder()
            source_texts = [c.text for c in source_chunks]

            all_texts = [response] + source_texts
            vectors = embedder.embed(all_texts)
            resp_vec = vectors[0]

            max_sim = 0.0
            for src_vec in vectors[1:]:
                sim = sum(a * b for a, b in zip(resp_vec, src_vec))
                max_sim = max(max_sim, sim)

            if max_sim >= self.threshold:
                return GuardrailResult(
                    passed=True,
                    action="allow",
                    message=f"Response grounded in source (max similarity={max_sim:.3f})",
                )

            if not use_llm:
                return GuardrailResult(
                    passed=False,
                    action="escalate",
                    message=f"Low similarity to source chunks ({max_sim:.3f} < {self.threshold}) — possible hallucination",
                )

            return self._llm_judge(response, source_texts, max_sim)
        except Exception as e:
            log.warning("Hallucination check failed: %s", e)
            return GuardrailResult(
                passed=True,
                action="allow",
                message=f"Hallucination check error — allowing: {e}",
            )

    def _llm_judge(self, response: str, source_texts: list[str], similarity: float) -> GuardrailResult:
        key = config.nvidia_api_key()
        if not key:
            return GuardrailResult(
                passed=False, action="escalate",
                message=f"Low similarity ({similarity:.3f}) and no API key for LLM judge",
            )

        try:
            resp = httpx.post(
                f"{config.nvidia_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.llm_model(),
                    "messages": [
                        {
                            "role": "system",
                            "content": "You check if an answer is factually supported by the provided context. JSON only.",
                        },
                        {
                            "role": "user",
                            "content": (
                                f'Answer JSON: {{"is_grounded": true/false, "reasoning": "..."}}\n\n'
                                f"Context:\n{chr(10).join(source_texts[:3])}\n\nAnswer: {response}"
                            ),
                        },
                    ],
                    "max_tokens": 256,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=config.llm_timeout(),
            )
            resp.raise_for_status()
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            if data.get("is_grounded", False):
                return GuardrailResult(passed=True, action="allow", message="LLM judge: response is grounded")
            return GuardrailResult(
                passed=False, action="escalate",
                message=f"LLM judge: response not grounded — {data.get('reasoning', '')}",
            )
        except Exception as e:
            log.warning("LLM judge failed: %s", e)
            return GuardrailResult(
                passed=False, action="escalate",
                message=f"Similarity={similarity:.3f} and LLM judge error: {e}",
            )


class AdviceBlocker:
    def __init__(self):
        self._patterns = ADVICE_PATTERNS

    def check(self, response: str, source_chunks: list[Chunk] | None = None) -> GuardrailResult:
        for compiled, category in self._patterns:
            m = compiled.search(response)
            if not m:
                continue

            # If source content is available, skip blocking when the matched
            # text comes from Stratpoint's own pages (descriptive, not advisory).
            if source_chunks:
                matched_text = m.group(0).lower()
                source_text = " ".join(c.text for c in source_chunks).lower()
                if matched_text in source_text:
                    return GuardrailResult(
                        passed=True,
                        action="allow",
                        message=f"'{category}' matched but present in source — allowing",
                    )

            return GuardrailResult(
                passed=False,
                action="block",
                message=f"Response contains {category} — blocked",
                modified_output=(
                    "I can only provide information about Stratpoint's services and "
                    "technology topics. Please consult a qualified professional for "
                    "professional advice."
                ),
            )
        return GuardrailResult(passed=True, action="allow", message="No advice patterns detected")


class OutputPipeline:
    def __init__(
        self,
        pii_checker: OutputPIIChecker | None = None,
        hallucination_checker: HallucinationChecker | None = None,
        advice_blocker: AdviceBlocker | None = None,
    ):
        self.pii_checker = pii_checker or OutputPIIChecker()
        self.hallucination_checker = hallucination_checker or HallucinationChecker()
        self.advice_blocker = advice_blocker or AdviceBlocker()

    def run(
        self,
        response: str,
        source_chunks: list[Chunk],
    ) -> tuple[str, list[GuardrailResult]]:
        results: list[GuardrailResult] = []

        advice_result = self.advice_blocker.check(response, source_chunks)
        results.append(advice_result)
        if not advice_result.passed:
            return advice_result.modified_output or response, results

        hallucination_result = self.hallucination_checker.check(response, source_chunks)
        results.append(hallucination_result)

        pii_result = self.pii_checker.check(response, source_chunks)
        results.append(pii_result)

        final_output = response
        if pii_result.modified_output:
            final_output = pii_result.modified_output

        return final_output, results
