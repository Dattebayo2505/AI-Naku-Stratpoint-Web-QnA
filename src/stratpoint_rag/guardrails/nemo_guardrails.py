from __future__ import annotations

import logging
import os

from nemoguardrails import LLMRails, RailsConfig
from nemoguardrails.rails.llm.llmrails import RailType

from stratpoint_rag.rag.models import Chunk

from .nemo import actions  # noqa: F401 — registers custom actions with NeMo
from .schemas import GuardrailConfig, GuardrailResult

log = logging.getLogger(__name__)

_NEMO_DIR = os.path.join(os.path.dirname(__file__), "nemo")


class NeMoGuardrailPipeline:
    """Wrapper around NeMo Guardrails that matches our GuardrailPipeline interface."""

    def __init__(self, config: GuardrailConfig | None = None):
        self.config = config or GuardrailConfig()
        self._rails: LLMRails | None = None
        self._config_path = _NEMO_DIR

    @property
    def rails(self) -> LLMRails:
        if self._rails is None:
            self._rails = self._build_rails()
        return self._rails

    def _build_rails(self) -> LLMRails:
        cfg = RailsConfig.from_path(self._config_path)
        rails = LLMRails(cfg)
        return rails

    def run_input(self, user_input: str) -> tuple[str, list[GuardrailResult]]:
        messages = [{"role": "user", "content": user_input}]
        try:
            result = self.rails.check(messages=messages, rail_types=[RailType.INPUT])
            modified = user_input

            neemo_results: list[GuardrailResult] = []
            if result.exception is not None:
                neemo_results.append(
                    GuardrailResult(
                        passed=False,
                        action="block",
                        message=f"NeMo input rail blocked: {result.exception.message}",
                    )
                )
            else:
                neemo_results.append(
                    GuardrailResult(
                        passed=True,
                        action="allow",
                        message="NeMo input rails passed",
                    )
                )

            if self.config.mode == "fail_closed":
                for r in neemo_results:
                    if r.action == "block":
                        return user_input, neemo_results

            return modified, neemo_results

        except Exception as e:
            log.warning("NeMo input rails failed: %s", e)
            return user_input, [
                GuardrailResult(
                    passed=True,
                    action="allow",
                    message=f"NeMo input rail error, allowing: {e}",
                )
            ]

    def run_output(
        self,
        llm_response: str,
        source_chunks: list[Chunk],
    ) -> tuple[str, list[GuardrailResult]]:
        messages = [
            {"role": "user", "content": "previous user query"},
            {"role": "assistant", "content": llm_response},
        ]
        try:
            result = self.rails.check(messages=messages, rail_types=[RailType.OUTPUT])
            modified = llm_response

            neemo_results: list[GuardrailResult] = []
            if result.exception is not None:
                neemo_results.append(
                    GuardrailResult(
                        passed=False,
                        action="block" if self.config.mode == "fail_closed" else "escalate",
                        message=f"NeMo output rail blocked: {result.exception.message}",
                    )
                )
            else:
                neemo_results.append(
                    GuardrailResult(
                        passed=True,
                        action="allow",
                        message="NeMo output rails passed",
                    )
                )

            for r in neemo_results:
                if r.action in ("block", "escalate") and self.config.mode == "fail_closed":
                    return llm_response, neemo_results

            return modified, neemo_results

        except Exception as e:
            log.warning("NeMo output rails failed: %s", e)
            return llm_response, [
                GuardrailResult(
                    passed=True,
                    action="allow",
                    message=f"NeMo output rail error, allowing: {e}",
                )
            ]
