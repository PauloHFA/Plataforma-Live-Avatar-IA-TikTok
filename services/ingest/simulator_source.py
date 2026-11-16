"""Simulador determinístico/configurável de eventos de live (obrigatório em 7.1)."""

from __future__ import annotations

import asyncio
import random

from services.ingest.base import LiveSource
from services.models import (
    EventPayload,
    EventType,
    EventUser,
    GiftPayload,
    NormalizedEvent,
)


class SimulatorSource(LiveSource):
    """Gera chats, presentes (incluindo combos), follows, curtidas e picos de carga."""

    DEFAULT_COMMENTS = [
        "Oi Luna, qual seu jogo favorito?",
        "Manda um salve pra galera de São Paulo!",
        "Essa live tá muito divertida hoje!",
        "Luna, o que você acha de jogos espaciais?",
        "!danca",
        "!cor #6c5ce7",
        "Quantos fps tá rodando essa nave?",
        "Primeiro dia aqui na live, gostei demais!",
    ]

    DEFAULT_GIFTS = [
        ("Rose", 1),
        ("TikTok", 5),
        ("Perfume", 20),
        ("Confetti", 100),
        ("Galaxy", 1000),
    ]

    def __init__(
        self,
        *,
        events_per_second: float = 1.0,
        max_events: int | None = None,
        seed: int = 42,
        include_streaks: bool = True,
        scripted_events: list[NormalizedEvent] | None = None,
    ) -> None:
        super().__init__()
        self.events_per_second = max(0.1, events_per_second)
        self.max_events = max_events
        self._rng = random.Random(seed)
        self.include_streaks = include_streaks
        self._scripted_events = list(scripted_events or [])
        self._stop_event = asyncio.Event()

    async def connect(self) -> None:
        self._connected = True
        self._stop_event.clear()
        emitted = 0

        if self._scripted_events:
            for event in self._scripted_events:
                if self._closed or self._stop_event.is_set():
                    break
                await self.emit_event(event)
                emitted += 1
                if self.max_events is not None and emitted >= self.max_events:
                    break
                await asyncio.sleep(1.0 / self.events_per_second)
            self._connected = False
            self._closed = True
            return

        interval = 1.0 / self.events_per_second
        while not self._closed and not self._stop_event.is_set():
            if self.max_events is not None and emitted >= self.max_events:
                self._closed = True
                break

            event = self._next_synthetic_event(emitted)
            await self.emit_event(event)
            emitted += 1

            # Emite também um combo intermediário + final periodicamente
            if self.include_streaks and emitted % 15 == 0:
                user = EventUser(id="7999001", nick="DoadorCombo", is_follower=True)
                await self.emit_event(
                    NormalizedEvent(
                        type=EventType.GIFT,
                        user=user,
                        payload=EventPayload(
                            gift=GiftPayload(name="Rose", coins=3, repeat=3, streak_final=False)
                        ),
                    )
                )
                await self.emit_event(
                    NormalizedEvent(
                        type=EventType.GIFT,
                        user=user,
                        payload=EventPayload(
                            gift=GiftPayload(name="Rose", coins=5, repeat=5, streak_final=True)
                        ),
                    )
                )

            await asyncio.sleep(interval)

        self._connected = False

    def _next_synthetic_event(self, index: int) -> NormalizedEvent:
        user_idx = (index % 12) + 1
        user = EventUser(
            id=str(7100000000 + user_idx),
            nick=f"Espectador{user_idx}",
            is_follower=user_idx % 2 == 0,
            is_moderator=user_idx == 1,
        )
        roll = self._rng.random()
        if roll < 0.58:
            text = self._rng.choice(self.DEFAULT_COMMENTS)
            return NormalizedEvent(
                type=EventType.CHAT,
                user=user,
                payload=EventPayload(text=text),
            )
        if roll < 0.75:
            gift_name, base_coins = self._rng.choice(self.DEFAULT_GIFTS)
            return NormalizedEvent(
                type=EventType.GIFT,
                user=user,
                payload=EventPayload(
                    gift=GiftPayload(
                        name=gift_name,
                        coins=base_coins,
                        repeat=1,
                        streak_final=True,
                    )
                ),
            )
        if roll < 0.85:
            return NormalizedEvent(type=EventType.FOLLOW, user=user)
        if roll < 0.92:
            return NormalizedEvent(type=EventType.SHARE, user=user)
        if roll < 0.97:
            return NormalizedEvent(
                type=EventType.LIKE,
                user=user,
                payload=EventPayload(likes=self._rng.randint(5, 30)),
            )
        return NormalizedEvent(type=EventType.JOIN, user=user)

    async def close(self) -> None:
        self._closed = True
        self._connected = False
        self._stop_event.set()
        await self.flush_pending_streaks()
