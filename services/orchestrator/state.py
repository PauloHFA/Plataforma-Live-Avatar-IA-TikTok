"""Máquina de estados do avatar, memória curta/sessão, anti-repetição e teto de custo."""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from services.models import AvatarState, Emotion, IdleConfig, IdleTopic


@dataclass
class DonorRecord:
    user_id: str
    nick: str
    total_coins: int = 0


@dataclass
class SessionMemory:
    """Memória de curto prazo (6-10 interações) e resumo rotativo da sessão (Seção 10.3)."""

    short_term_window: int = 8
    recent_exchanges: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=8))
    recent_utterances: deque[str] = field(default_factory=lambda: deque(maxlen=15))
    donors: dict[str, DonorRecord] = field(default_factory=dict)
    recent_gifts: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=10))
    hall_of_live: list[dict[str, object]] = field(default_factory=list)
    session_summary: str = "Live recém-iniciada com a comunidade."
    total_coins: int = 0
    last_summary_at: float = field(default_factory=time.monotonic)

    def add_exchange(self, user_nick: str, user_text: str, assistant_text: str) -> None:
        if self.recent_exchanges.maxlen != self.short_term_window:
            self.recent_exchanges = deque(self.recent_exchanges, maxlen=self.short_term_window)
        self.recent_exchanges.append(
            {"user": user_nick, "input": user_text, "assistant": assistant_text}
        )
        self.recent_utterances.append(assistant_text.strip().lower())

    def is_repetitive(self, candidate: str, threshold: float = 0.82) -> bool:
        cand = candidate.strip().lower()
        if not cand:
            return True
        for prev in self.recent_utterances:
            if cand == prev:
                return True
            if SequenceMatcher(None, cand, prev).ratio() >= threshold:
                return True
        return False

    def record_gift(
        self,
        *,
        user_id: str,
        nick: str,
        gift_name: str,
        coins: int,
        repeat: int,
        tier: int,
        hall_of_live: bool = False,
    ) -> int:
        rec = self.donors.get(user_id)
        if rec is None:
            rec = DonorRecord(user_id=user_id, nick=nick, total_coins=coins)
            self.donors[user_id] = rec
        else:
            rec.nick = nick
            rec.total_coins += coins
        self.total_coins += coins

        entry = {
            "user_id": user_id,
            "nick": nick,
            "gift": gift_name,
            "coins": coins,
            "repeat": repeat,
            "tier": tier,
        }
        self.recent_gifts.appendleft(entry)
        if hall_of_live:
            self.hall_of_live.append(entry)
        return rec.total_coins

    def top_donors(self, limit: int = 5) -> list[dict[str, object]]:
        ranked = sorted(self.donors.values(), key=lambda d: d.total_coins, reverse=True)
        return [
            {"user_id": d.user_id, "nick": d.nick, "total_coins": d.total_coins}
            for d in ranked[:limit]
        ]

    def is_donor(self, user_id: str) -> bool:
        return user_id in self.donors

    def refresh_summary(self) -> str:
        top = self.top_donors(3)
        top_txt = (
            ", ".join(f"{d['nick']} ({d['total_coins']} moedas)" for d in top) or "nenhum ainda"
        )
        recent_inputs = [x["input"] for x in list(self.recent_exchanges)[-4:]]
        topics_txt = "; ".join(recent_inputs) if recent_inputs else "boas-vindas"
        self.session_summary = (
            f"Total acumulado: {self.total_coins} moedas. Top doadores: {top_txt}. "
            f"Assuntos recentes: {topics_txt}."
        )
        self.last_summary_at = time.monotonic()
        return self.session_summary


class IdleTopicRotator:
    """Seleção ponderada e sem repetição imediata para modo ocioso (RF-12, CA-11)."""

    def __init__(self, config: IdleConfig, seed: int = 42) -> None:
        self.config = config
        self._rng = random.Random(seed)
        self._used_recent: deque[str] = deque(maxlen=max(1, len(config.topics) - 1))

    def next_topic(self) -> IdleTopic:
        if not self.config.topics:
            return IdleTopic(
                id="default_idle",
                text="Tô de olho no chat! Manda uma pergunta ou um !danca pra agitar a nave!",
            )
        candidates = [t for t in self.config.topics if t.id not in self._used_recent]
        if not candidates:
            self._used_recent.clear()
            candidates = list(self.config.topics)
        weights = [t.weight for t in candidates]
        chosen = self._rng.choices(candidates, weights=weights, k=1)[0]
        self._used_recent.append(chosen.id)
        return chosen


class AvatarStateMachine:
    """Controla estado atual, pausa, kill switch, cena, humor e orçamento diário."""

    def __init__(self, *, daily_budget_usd: float = 5.0) -> None:
        self.state: AvatarState = AvatarState.IDLE
        self.paused: bool = False
        self.safe_mode: bool = False
        self.mood: Emotion = Emotion.ALEGRE
        self.scene: str = "studio_space"
        self.current_utterance_id: str | None = None
        self.last_activity_at: float = time.monotonic()

        self.daily_budget_usd = daily_budget_usd
        self.spent_usd: float = 0.0
        self.budget_warned: bool = False
        self.economy_mode: bool = False

    def transition(self, new_state: AvatarState) -> None:
        self.state = new_state
        self.last_activity_at = time.monotonic()

    def engage_kill_switch(self) -> None:
        self.safe_mode = True
        self.paused = True
        self.current_utterance_id = None
        self.transition(AvatarState.SAFE_MODE)

    def disengage_kill_switch(self) -> None:
        self.safe_mode = False
        self.paused = False
        self.transition(AvatarState.IDLE)

    def add_cost(self, amount_usd: float) -> tuple[bool, bool]:
        """Registra custo e retorna (alerta_80_disparado, corte_100_ativado)."""
        if amount_usd <= 0 or self.daily_budget_usd <= 0:
            return (False, False)
        self.spent_usd += amount_usd
        ratio = self.spent_usd / self.daily_budget_usd
        warn_now = False
        cutoff_now = False
        if ratio >= 0.8 and not self.budget_warned:
            self.budget_warned = True
            warn_now = True
        if ratio >= 1.0 and not self.economy_mode:
            self.economy_mode = True
            cutoff_now = True
        return (warn_now, cutoff_now)
