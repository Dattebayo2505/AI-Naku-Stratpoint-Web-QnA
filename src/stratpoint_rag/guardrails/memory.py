from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Turn:
    role: str
    content: str


@dataclass
class ConversationMemory:
    session_id: str
    max_turns: int = 6
    turns: list[Turn] = field(default_factory=list)
    # Consecutive turns the bot could not answer (clarification asked or answer
    # ungrounded). Drives the F3 escalation hand-off. Reset on any real answer.
    clarify_streak: int = 0

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(Turn(role=role, content=content))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def get_context(self, query: str | None = None) -> str:
        if not self.turns:
            return ""
        lines = [f"{t.role}: {t.content}" for t in self.turns]
        return "\n".join(lines)

    def clear(self) -> None:
        self.turns.clear()

    @property
    def is_empty(self) -> bool:
        return len(self.turns) == 0

    @property
    def turn_count(self) -> int:
        return len(self.turns)
