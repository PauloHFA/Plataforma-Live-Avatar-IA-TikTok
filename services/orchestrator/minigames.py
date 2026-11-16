"""Minijogos e comandos de chat (!danca, !cor, !votar, !quiz) - RF-19."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class PollState:
    question: str = "Qual clima do estúdio agora?"
    option_a: str = "Espacial"
    option_b: str = "Neon"
    votes_a: set[str] = field(default_factory=set)
    votes_b: set[str] = field(default_factory=set)


@dataclass
class MinigameResult:
    handled: bool
    speech: str | None = None
    action: str | None = None
    color_hex: str | None = None
    poll_snapshot: dict[str, object] | None = None


COLOR_MAP = {
    "azul": "#3b82f6",
    "roxo": "#8b5cf6",
    "rosa": "#ec4899",
    "verde": "#10b981",
    "vermelho": "#ef4444",
    "dourado": "#f59e0b",
}


class ChatMinigameEngine:
    """Processa comandos de chat com limite de uso por usuário."""

    def __init__(self, *, cooldown_seconds: float = 15.0) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last_cmd_by_user: dict[str, float] = {}
        self.poll = PollState()

    def handle_command(
        self,
        *,
        user_id: str,
        user_nick: str,
        text: str,
        now: float | None = None,
    ) -> MinigameResult:
        raw = text.strip()
        if not raw.startswith("!"):
            return MinigameResult(handled=False)

        now_ts = time.monotonic() if now is None else now
        last = self._last_cmd_by_user.get(user_id)
        if last is not None and (now_ts - last) < self.cooldown_seconds:
            return MinigameResult(handled=True)

        parts = raw.split()
        cmd = parts[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""

        if cmd in ("!danca", "!dança", "!dance"):
            self._last_cmd_by_user[user_id] = now_ts
            return MinigameResult(
                handled=True,
                speech=f"Bora dançar então, {user_nick}!",
                action="dance",
            )

        if cmd == "!cor" and arg:
            color = COLOR_MAP.get(arg)
            if color is None and re.fullmatch(r"#[0-9a-f]{6}", arg):
                color = arg
            if color is not None:
                self._last_cmd_by_user[user_id] = now_ts
                return MinigameResult(
                    handled=True,
                    speech=f"Luzes do estúdio trocadas a pedido de {user_nick}!",
                    color_hex=color,
                )

        if cmd in ("!votar", "!voto") and arg in ("a", "b"):
            self._last_cmd_by_user[user_id] = now_ts
            if arg == "a":
                self.poll.votes_b.discard(user_id)
                self.poll.votes_a.add(user_id)
            else:
                self.poll.votes_a.discard(user_id)
                self.poll.votes_b.add(user_id)
            return MinigameResult(
                handled=True,
                poll_snapshot={
                    "question": self.poll.question,
                    "a": {"label": self.poll.option_a, "votes": len(self.poll.votes_a)},
                    "b": {"label": self.poll.option_b, "votes": len(self.poll.votes_b)},
                },
            )

        return MinigameResult(handled=False)
