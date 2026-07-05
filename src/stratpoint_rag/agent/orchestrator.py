from __future__ import annotations

import json
import logging

import httpx

from stratpoint_rag.disambiguation.router import route
from stratpoint_rag.guardrails.memory import ConversationMemory
from stratpoint_rag.guardrails.pipeline import GuardrailPipeline
from stratpoint_rag.guardrails.schemas import GuardrailConfig, GuardrailResult
from stratpoint_rag.prompts.builder import build_prompt
from stratpoint_rag.prompts.schema import Citation, GroundedAnswer
from stratpoint_rag.rag import config
from stratpoint_rag.rag.models import Chunk
from stratpoint_rag.rag.retrieve import retrieve

from pydantic import BaseModel

log = logging.getLogger(__name__)


class AnswerResult(BaseModel):
    answer: str
    citations: list[Citation] = []
    intent: str = ""
    guardrail_results: list[GuardrailResult] = []
    clarification_needed: bool = False
    clarification_question: str | None = None
    session_id: str | None = None


class Agent:
    def __init__(
        self,
        guardrail_config: GuardrailConfig | None = None,
        use_nemo: bool = False,
    ):
        self.guardrail_config = guardrail_config or GuardrailConfig()
        self.use_nemo = use_nemo
        if use_nemo:
            from stratpoint_rag.guardrails.nemo_guardrails import NeMoGuardrailPipeline
            self.guardrails = NeMoGuardrailPipeline(self.guardrail_config)
        else:
            self.guardrails = GuardrailPipeline(self.guardrail_config)
        self._memories: dict[str, ConversationMemory] = {}

    def orchestrate(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> AnswerResult:
        memory = self._get_or_create_memory(session_id)

        processed_input, input_results = self.guardrails.run_input(user_input)

        for r in input_results:
            if r.action == "block" and self.guardrail_config.mode == "fail_closed":
                return AnswerResult(
                    answer=r.message,
                    intent="blocked",
                    guardrail_results=input_results,
                    session_id=session_id,
                )

        route_result = route(processed_input, session_id=session_id)

        if route_result.clarification_question:
            return AnswerResult(
                answer="",
                intent=route_result.intent.value,
                guardrail_results=input_results,
                clarification_needed=True,
                clarification_question=route_result.clarification_question,
                session_id=session_id,
            )

        if not route_result.should_retrieve and route_result.rejection_reason:
            return AnswerResult(
                answer=route_result.rejection_reason,
                intent=route_result.intent.value,
                guardrail_results=input_results,
                session_id=session_id,
            )

        chunks = retrieve(route_result.query, k=5)

        memory_context = memory.get_context(user_input)

        memory_context_block = ""
        if memory_context:
            memory_context_block = f"Conversation history:\n{memory_context}\n\n"

        system_prompt, user_prompt = build_prompt(
            route_result.query,
            chunks,
            variant="v4_combined_lowtemp",
        )

        if memory_context_block:
            user_prompt = f"{memory_context_block}{user_prompt}"

        try:
            raw_response = self._call_llm(system_prompt, user_prompt)

            final_output, output_results = self.guardrails.run_output(raw_response, chunks)

            for r in output_results:
                if r.action in ("block", "escalate") and self.guardrail_config.mode == "fail_closed":
                    return AnswerResult(
                        answer=(
                            "I generated a response, but it failed safety checks. "
                            "Please rephrase your question or contact our team for assistance."
                        ),
                        intent=route_result.intent.value,
                        guardrail_results=input_results + output_results,
                        session_id=session_id,
                    )

            parsed = GroundedAnswer.model_validate_json(final_output)
            text = self._format_answer(parsed)

            memory.add_turn(user_input, text)

            return AnswerResult(
                answer=text,
                citations=parsed.citations,
                intent=route_result.intent.value,
                guardrail_results=input_results + output_results,
                session_id=session_id,
            )

        except Exception as e:
            log.exception("Agent orchestration failed")
            return AnswerResult(
                answer=f"An error occurred: {e}",
                intent=route_result.intent.value,
                guardrail_results=input_results,
                session_id=session_id,
            )

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        key = config.nvidia_api_key()
        if not key:
            raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")

        resp = httpx.post(
            f"{config.nvidia_base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": config.llm_model(),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.1,
                "top_p": 0.95,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _format_answer(self, parsed: GroundedAnswer) -> str:
        text = parsed.answer

        if parsed.citations:
            citations_list = []
            for c in parsed.citations:
                title = c.title if c.title else "Stratpoint"
                citations_list.append(f"- {title} ({c.url})")
            citations_str = "\n\nSources used:\n" + "\n".join(citations_list)
            return f"{text}{citations_str}"

        return text

    def _get_or_create_memory(self, session_id: str | None = None) -> ConversationMemory:
        sid = session_id or "default"
        if sid not in self._memories:
            self._memories[sid] = ConversationMemory(session_id=sid)
        return self._memories[sid]

    def clear_memory(self, session_id: str | None = None) -> None:
        sid = session_id or "default"
        self._memories.pop(sid, None)
