from __future__ import annotations

import logging

from stratpoint_rag.rag.models import Chunk

from .input_guardrails import InputPipeline
from .output_guardrails import OutputPipeline
from .schemas import GuardrailConfig, GuardrailResult

log = logging.getLogger(__name__)


class GuardrailPipeline:
    def __init__(self, config: GuardrailConfig | None = None):
        self.config = config or GuardrailConfig()
        self.input_pipeline = InputPipeline(
            redactor=None,
            topic_filter=None if not self.config.filter_topic else None,
            keyword_blocker=None if not self.config.block_keywords else None,
        )
        self.output_pipeline = OutputPipeline(
            pii_checker=None,
            hallucination_checker=None if not self.config.check_hallucination else None,
            advice_blocker=None if not self.config.check_advice else None,
        )

    def run_input(self, user_input: str) -> tuple[str, list[GuardrailResult]]:
        modified_input, results = self.input_pipeline.run(user_input)

        if self.config.mode == "fail_closed":
            for r in results:
                if r.action == "block":
                    return user_input, results

        return modified_input, results

    def run_output(
        self,
        llm_response: str,
        source_chunks: list[Chunk],
    ) -> tuple[str, list[GuardrailResult]]:
        modified_output, results = self.output_pipeline.run(llm_response, source_chunks)

        if self.config.mode == "fail_closed":
            for r in results:
                if r.action in ("block", "escalate"):
                    return llm_response, results

        return modified_output, results
