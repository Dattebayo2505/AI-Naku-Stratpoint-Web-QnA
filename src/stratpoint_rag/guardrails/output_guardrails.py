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


class OutputPIIChecker:
    def __init__(self):
        self.redactor = PIIRedactor()

    def check(self, response: str, source_chunks: list[Chunk]) -> GuardrailResult:
        source_text = " ".join(c.text for c in source_chunks)
        redacted, matched_rules = self.redactor.redact(response)

        if not matched_rules:
            return GuardrailResult(passed=True, action="allow", message="No PII detected in output")

        if matched_rules:
            for rule in matched_rules:
                if re.search(rule.pattern, source_text):
                    return GuardrailResult(
                        passed=True,
                        action="allow",
                        message=(
                            f"PII pattern '{rule.entity_type}' found in output "
                            f"but also present in source documents — likely legitimate"
                        ),
                        modified_output=response,
                    )

            return GuardrailResult(
                passed=False,
                action="redact",
                message=f"PII leaked in output: {[r.entity_type for r in matched_rules]}",
                modified_output=redacted,
            )

        return GuardrailResult(passed=True, action="allow", message="No PII detected")


class HallucinationChecker:
    def __init__(self, llm_threshold: float = 0.7, semantic_threshold: float = 0.75):
        self.llm_threshold = llm_threshold
        self.semantic_threshold = semantic_threshold
        self._embedder = None

    def check(self, response: str, source_chunks: list[Chunk]) -> GuardrailResult:
        llm_flags = self._llm_judge(response, source_chunks)
        semantic_flags = self._semantic_check(response, source_chunks)

        if llm_flags and semantic_flags:
            return GuardrailResult(
                passed=False,
                action="escalate",
                message=(
                    f"Hallucination detected by both checks. "
                    f"LLM flags: {llm_flags[:200]}... | Semantic flags: {semantic_flags:.1%} unsupported claims"
                ),
            )

        if llm_flags or semantic_flags:
            modified = response + (
                "\n\n---\n"
                "**Note:** Some claims in this answer could not be fully verified against our "
                "source documents. Please verify important information before acting on it."
            )
            return GuardrailResult(
                passed=True,
                action="allow",
                message=(
                    f"One check flagged potential hallucination (LLM: {bool(llm_flags)}, "
                    f"Semantic: {bool(semantic_flags)}). Allowing with warning appended."
                ),
                modified_output=modified,
            )

        return GuardrailResult(passed=True, action="allow", message="No hallucination detected")

    def _llm_judge(self, response: str, source_chunks: list[Chunk]) -> str | None:
        source_text = "\n---\n".join(f"[{c.title}]({c.url})\n{c.text}" for c in source_chunks[:5])

        prompt = (
            f"You are a fact-checker. Verify if the following answer is fully supported by the provided sources.\n\n"
            f"SOURCES:\n{source_text}\n\n"
            f"ANSWER:\n{response}\n\n"
            f"List any factual claims in the answer that are NOT supported by the sources. "
            f"If all claims are supported, respond with: {{\"unsupported_claims\": []}}\n"
            f"Otherwise: {{\"unsupported_claims\": [\"claim 1\", \"claim 2\", ...], "
            f"\"reasoning\": \"explanation of why each claim is unsupported\"}}"
        )

        try:
            key = config.nvidia_api_key()
            if not key:
                return None

            resp = httpx.post(
                f"{config.nvidia_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.llm_model(),
                    "messages": [
                        {"role": "system", "content": "You are a strict fact-checker. Respond with JSON only."},
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
            data = json.loads(raw)
            claims = data.get("unsupported_claims", [])
            if claims:
                return json.dumps(claims[:3])
            return None
        except Exception as e:
            log.warning("LLM hallucination judge failed: %s", e)
            return None

    def _semantic_check(self, response: str, source_chunks: list[Chunk]) -> float:
        try:
            import re as _re

            sentences = _re.split(r'(?<=[.!?])\s+', response)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

            if not sentences or not source_chunks:
                return 0.0

            if self._embedder is None:
                self._embedder = get_embedder()

            source_embeddings = self._embedder.embed([c.text for c in source_chunks])
            sentence_embeddings = self._embedder.embed(sentences)

            flagged_count = 0
            for sent_emb in sentence_embeddings:
                best_sim = 0.0
                for src_emb in source_embeddings:
                    sim = self._cosine_similarity(sent_emb, src_emb)
                    if sim > best_sim:
                        best_sim = sim
                if best_sim < self.semantic_threshold:
                    flagged_count += 1

            return flagged_count / len(sentences) if sentences else 0.0
        except Exception as e:
            log.warning("Semantic hallucination check failed: %s", e)
            return 0.0

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class AdviceBlocker:
    ADVICE_PATTERNS: list[re.Pattern] = [
        re.compile(r"this is (not )?(legal|medical|financial|investment) advice", re.IGNORECASE),
        re.compile(r"consult (a|your) (doctor|lawyer|attorney|financial advisor|professional)", re.IGNORECASE),
        re.compile(r"I am (not |no(t a)? )(a )?(doctor|lawyer|attorney|financial advisor|medical professional)", re.IGNORECASE),
        re.compile(r"you should (consult|contact|see|talk to) (a|your) (doctor|lawyer|attorney)", re.IGNORECASE),
        re.compile(r"diagnos(is|e)", re.IGNORECASE),
        re.compile(r"prescribe|medication|dosage", re.IGNORECASE),
        re.compile(r"investment (advice|recommendation|strategy)", re.IGNORECASE),
        re.compile(r"stock (pick|tip|recommendation)", re.IGNORECASE),
        re.compile(r"buy/sell|sell/buy", re.IGNORECASE),
    ]

    def check(self, response: str) -> GuardrailResult:
        matched = []
        for pattern in self.ADVICE_PATTERNS:
            if pattern.search(response):
                matched.append(pattern.pattern)

        if matched:
            return GuardrailResult(
                passed=False,
                action="escalate",
                message=f"Unauthorized advice patterns detected in output ({len(matched)} matches)",
                modified_output=response + (
                    "\n\n---\n**Disclaimer**: The above response may contain patterns resembling "
                    "professional advice. Stratpoint's chatbot should not provide legal, medical, "
                    "or financial advice. Please verify with a qualified professional."
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

        pii_result = self.pii_checker.check(response, source_chunks)
        results.append(pii_result)
        final_output = pii_result.modified_output if pii_result.modified_output else response

        advice_result = self.advice_blocker.check(final_output)
        results.append(advice_result)
        if advice_result.modified_output:
            final_output = advice_result.modified_output

        hallu_result = self.hallucination_checker.check(final_output, source_chunks)
        results.append(hallu_result)
        if hallu_result.modified_output:
            final_output = hallu_result.modified_output

        return final_output, results
