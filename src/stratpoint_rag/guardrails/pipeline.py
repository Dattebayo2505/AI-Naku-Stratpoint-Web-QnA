from __future__ import annotations

import logging

from stratpoint_rag.rag.models import Chunk

from .input_guardrails import InputPipeline
from .output_guardrails import OutputPipeline
from .schemas import GuardrailConfig, GuardrailResult

log = logging.getLogger(__name__)


class GuardrailPipeline:
    def __init__(self, config: GuardrailConfig | None = None):
        cfg = config or GuardrailConfig()
        self.config = cfg
        self.input_pipeline = InputPipeline() if cfg.filter_topic or cfg.redact_pii or cfg.block_keywords else None
        self.output_pipeline = OutputPipeline() if cfg.check_hallucination or cfg.check_advice else None

    def run_input(self, user_input: str) -> tuple[str, list[GuardrailResult]]:
        if self.input_pipeline is None:
            return user_input, [GuardrailResult(passed=True, action="allow", message="All input checks disabled")]
        return self.input_pipeline.run(user_input)

    def run_output(
        self, response: str, source_chunks: list[Chunk]
    ) -> tuple[str, list[GuardrailResult]]:
        if self.output_pipeline is None:
            return response, [GuardrailResult(passed=True, action="allow", message="All output checks disabled")]
        return self.output_pipeline.run(response, source_chunks)
