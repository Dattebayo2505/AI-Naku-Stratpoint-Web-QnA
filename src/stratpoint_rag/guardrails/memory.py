from __future__ import annotations

import json
import logging
import uuid

import httpx

from stratpoint_rag.rag import config
from stratpoint_rag.rag.embeddings import get_embedder

log = logging.getLogger(__name__)

_SUMMARY_PROMPT = """Summarize the following conversation between a User and a Stratpoint customer-support assistant. Keep the summary concise (2-4 sentences), capturing the key topics, questions, and answers.

Previous summary: {previous_summary}

New turns:
{new_turns}

Write a concise updated summary:"""


class SummaryBuffer:
    """Custom ConversationSummaryBufferMemory — no LangChain dependency.

    Maintains a running summary of the conversation. When a token budget
    is exceeded, the buffer requests the LLM to compress old turns into
    a summary while keeping recent turns verbatim.
    """

    def __init__(self, max_token_limit: int = 2000):
        self.max_token_limit = max_token_limit
        self.running_summary: str = ""
        self._recent_turns: list[dict] = []

    def add_turn(self, user_input: str, assistant_response: str) -> None:
        turn = {"input": user_input, "output": assistant_response}
        self._recent_turns.append(turn)
        self._maybe_compress()

    def load_memory(self) -> str:
        parts = []
        if self.running_summary:
            parts.append(f"Conversation summary: {self.running_summary}")
        if self._recent_turns:
            parts.append("Recent messages:")
            for t in self._recent_turns:
                parts.append(f"  User: {t['input']}")
                parts.append(f"  Assistant: {t['output']}")
        return "\n".join(parts)

    def _maybe_compress(self) -> None:
        estimated_tokens = self._estimate_tokens()
        if estimated_tokens <= self.max_token_limit:
            return

        turns_text = "\n".join(
            f"User: {t['input']}\nAssistant: {t['output']}"
            for t in self._recent_turns[:-1]
        )

        if turns_text and turns_text.strip():
            try:
                key = config.nvidia_api_key()
                if key:
                    resp = httpx.post(
                        f"{config.nvidia_base_url()}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": config.llm_model(),
                            "messages": [
                                {"role": "system", "content": "You summarize conversations."},
                                {
                                    "role": "user",
                                    "content": _SUMMARY_PROMPT.format(
                                        previous_summary=self.running_summary or "(none)",
                                        new_turns=turns_text,
                                    ),
                                },
                            ],
                            "max_tokens": 512,
                            "temperature": 0.1,
                            "stream": False,
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                    self.running_summary = resp.json()["choices"][0]["message"]["content"]
            except Exception as e:
                log.warning("Summary compression failed: %s", e)

        if self._recent_turns:
            self._recent_turns = [self._recent_turns[-1]]

    def _estimate_tokens(self) -> int:
        text = self.running_summary + "\n" + str(self._recent_turns)
        return len(text) // 4


class ConversationMemory:
    def __init__(
        self,
        session_id: str | None = None,
        chroma_collection=None,
        max_token_limit: int = 2000,
    ):
        self.session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        self.chroma_collection = chroma_collection
        self._embedder = None
        self._summary_buffer = SummaryBuffer(max_token_limit=max_token_limit)

    def add_turn(self, user_input: str, assistant_response: str) -> None:
        self._summary_buffer.add_turn(user_input, assistant_response)
        self.persist_turn(user_input, assistant_response)

    def get_summary(self) -> str:
        return self._summary_buffer.load_memory()

    def persist_turn(
        self,
        user_input: str,
        assistant_response: str,
        metadata: dict | None = None,
    ) -> None:
        if self.chroma_collection is None:
            return

        turn_text = f"User: {user_input}\nAssistant: {assistant_response}"
        meta = {
            "session_id": self.session_id,
            "type": "conversation_turn",
            **(metadata or {}),
        }

        try:
            if self._embedder is None:
                self._embedder = get_embedder()
            embedding = self._embedder.embed([turn_text])[0]
            self.chroma_collection.add(
                ids=[f"{self.session_id}_{uuid.uuid4().hex[:8]}"],
                embeddings=[embedding],
                metadatas=[meta],
                documents=[turn_text],
            )
        except Exception as e:
            log.warning("Failed to persist turn to ChromaDB: %s", e)

    def search_history(self, query: str, k: int = 3) -> list[dict]:
        if self.chroma_collection is None:
            return []

        try:
            if self._embedder is None:
                self._embedder = get_embedder()
            query_emb = self._embedder.embed([query])[0]
            results = self.chroma_collection.query(
                query_embeddings=[query_emb],
                n_results=k,
            )
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            return [
                {"text": doc, "metadata": meta}
                for doc, meta in zip(documents, metadatas)
            ]
        except Exception as e:
            log.warning("Failed to search history: %s", e)
            return []

    def get_context(self, current_query: str) -> str:
        summary = self.get_summary()
        history = self.search_history(current_query, k=3)

        parts = []

        if summary:
            parts.append(f"Previous conversation summary:\n{summary}")

        if history:
            hist_lines = []
            for h in history:
                text = h["text"][:200]
                hist_lines.append(f"  - {text}...")
            parts.append("Relevant past interactions:\n" + "\n".join(hist_lines))

        if parts:
            return "\n\n".join(parts)
        return ""
