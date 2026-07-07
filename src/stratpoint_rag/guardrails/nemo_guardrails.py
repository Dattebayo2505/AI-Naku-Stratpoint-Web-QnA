from __future__ import annotations

import logging
import os

from stratpoint_rag.rag import config as rag_config
from stratpoint_rag.rag.models import Chunk

from .schemas import GuardrailConfig, GuardrailResult

log = logging.getLogger(__name__)

_NEMO_DIR = os.path.join(os.path.dirname(__file__), "nemo")

try:
    from nemoguardrails import LLMRails, RailsConfig
    from nemoguardrails.rails.llm.llmrails import RailType

    import stratpoint_rag.guardrails.nemo.actions  # noqa: F401

    _HAS_NEMO = True
except ImportError:
    _HAS_NEMO = False
    log.warning("nemoguardrails not installed; NeMo backend unavailable")


class NeMoGuardrailPipeline:
    def __init__(self, config: GuardrailConfig | None = None):
        if not _HAS_NEMO:
            raise ImportError("nemoguardrails is not installed")
        self.config = config or GuardrailConfig()
        self._rails = None

    def _build_rails(self):
        cfg = RailsConfig.from_path(_NEMO_DIR)
        for m in cfg.models:
            if m.type == "main":
                m.model = rag_config.llm_model()
        return LLMRails(cfg)

    @property
    def rails(self):
        if self._rails is None:
            self._rails = self._build_rails()
        return self._rails

    def run_input(self, user_input: str) -> tuple[str, list[GuardrailResult]]:
        try:
            messages = [{"role": "user", "content": user_input}]
            result = self.rails.check(messages=messages, rail_types=[RailType.INPUT])

            if result.exception is not None:
                return user_input, [
                    GuardrailResult(
                        passed=False,
                        action="block" if self.config.mode == "fail_closed" else "escalate",
                        message=f"NeMo input blocked: {result.exception.message}",
                    )
                ]

            # NeMo inserts an assistant message when a rail fires (jailbreak,
            # self-check input, etc.).  If the response has more than the
            # original user message, treat it as blocked.
            if result.response and len(result.response) > len(messages):
                assistant_msgs = [
                    m for m in result.response if m.get("role") == "assistant"
                ]
                reason = (
                    assistant_msgs[-1].get("content", "NeMo blocked this input")
                    if assistant_msgs
                    else "NeMo blocked this input"
                )
                return user_input, [
                    GuardrailResult(
                        passed=False,
                        action="block",
                        message=f"NeMo input blocked: {reason[:200]}",
                    )
                ]

            return user_input, [
                GuardrailResult(passed=True, action="allow", message="NeMo input rails passed")
            ]
        except Exception as e:
            log.warning("NeMo input rails failed: %s", e)
            return user_input, [
                GuardrailResult(passed=True, action="allow", message=f"NeMo error — allowing: {e}")
            ]

    def run_output(self, llm_response: str, source_chunks: list[Chunk]) -> tuple[str, list[GuardrailResult]]:
        try:
            messages = [
                {"role": "user", "content": "previous user query"},
                {"role": "assistant", "content": llm_response},
            ]
            context = {"source_chunks": source_chunks}
            result = self.rails.check(messages=messages, rail_types=[RailType.OUTPUT], context=context)

            if result.exception is not None:
                return llm_response, [
                    GuardrailResult(
                        passed=False,
                        action="block" if self.config.mode == "fail_closed" else "escalate",
                        message=f"NeMo output blocked: {result.exception.message}",
                    )
                ]

            if result.response and len(result.response) > len(messages):
                assistant_msgs = [
                    m for m in result.response if m.get("role") == "assistant"
                ]
                reason = (
                    assistant_msgs[-1].get("content", "NeMo blocked this response")
                    if assistant_msgs
                    else "NeMo blocked this response"
                )
                return llm_response, [
                    GuardrailResult(
                        passed=False,
                        action="block",
                        message=f"NeMo output blocked: {reason[:200]}",
                    )
                ]

            return llm_response, [
                GuardrailResult(passed=True, action="allow", message="NeMo output rails passed")
            ]
        except Exception as e:
            log.warning("NeMo output rails failed: %s", e)
            return llm_response, [
                GuardrailResult(passed=True, action="allow", message=f"NeMo error — allowing: {e}")
            ]
