"""Fila de prioridades P0..P5, taxa por usuário, deduplicação e política de descarte."""

from __future__ import annotations

import heapq
import itertools
import time
from collections import deque
from dataclasses import dataclass, field

from services.models import EventType, NormalizedEvent, PriorityLevel


@dataclass(order=True)
class QueuedItem:
    priority: int
    seq: int
    enqueued_at: float = field(compare=False)
    event: NormalizedEvent = field(compare=False)
    is_donor: bool = field(default=False, compare=False)


class EventPriorityQueue:
    """Fila com prioridades P0..P5, TTL em P2, lote em P3 e rate limit por espectador."""

    def __init__(
        self,
        *,
        max_size: int = 200,
        user_cooldown_seconds: float = 35.0,
        p2_ttl_seconds: float = 20.0,
        p3_batch_interval_seconds: float = 15.0,
        mention_phrases: list[str] | None = None,
    ) -> None:
        self._heap: list[QueuedItem] = []
        self._counter = itertools.count()
        self.max_size = max_size
        self.user_cooldown_seconds = user_cooldown_seconds
        self.p2_ttl_seconds = p2_ttl_seconds
        self.p3_batch_interval_seconds = p3_batch_interval_seconds
        self.mention_phrases = [p.lower() for p in (mention_phrases or ["luna", "@luna"])]

        self._last_user_reply_at: dict[str, float] = {}
        self._recent_text_hashes: dict[tuple[str, str], float] = {}
        self._follow_share_batch: list[NormalizedEvent] = []
        self._last_p3_flush = time.monotonic()
        self._like_total = 0
        self.slow_mode = False
        self._arrival_timestamps: deque[float] = deque(maxlen=2000)

        self.dropped_count = 0
        self.dedup_dropped_count = 0

    def record_arrival(self, now: float | None = None) -> float:
        """Registra chegada e retorna taxa atual em eventos/s na janela de 5s."""
        now_ts = time.monotonic() if now is None else now
        self._arrival_timestamps.append(now_ts)
        cutoff = now_ts - 5.0
        while self._arrival_timestamps and self._arrival_timestamps[0] < cutoff:
            self._arrival_timestamps.popleft()
        return len(self._arrival_timestamps) / 5.0

    def evaluate_raid_protection(
        self,
        *,
        high_rate_threshold: float = 25.0,
        low_rate_threshold: float = 10.0,
        now: float | None = None,
    ) -> tuple[bool, float]:
        """Ativa/desativa slow_mode automaticamente; retorna (alterou, taxa_atual)."""
        now_ts = time.monotonic() if now is None else now
        cutoff = now_ts - 5.0
        while self._arrival_timestamps and self._arrival_timestamps[0] < cutoff:
            self._arrival_timestamps.popleft()
        rate = len(self._arrival_timestamps) / 5.0

        if not self.slow_mode and rate >= high_rate_threshold:
            self.slow_mode = True
            return True, rate
        if self.slow_mode and rate <= low_rate_threshold:
            self.slow_mode = False
            return True, rate
        return False, rate

    def classify_priority(self, event: NormalizedEvent) -> PriorityLevel:
        if event.type in (EventType.OPERATOR, EventType.SYSTEM):
            return PriorityLevel.P0_OPERATOR
        if event.type == EventType.GIFT:
            return PriorityLevel.P1_GIFT
        if event.type == EventType.CHAT:
            text = (event.payload.text or "").lower()
            if "?" in text or any(phrase in text for phrase in self.mention_phrases):
                return PriorityLevel.P2_MENTION_QUESTION
            return PriorityLevel.P4_CHAT
        if event.type in (EventType.FOLLOW, EventType.SHARE):
            return PriorityLevel.P3_FOLLOW_SHARE
        return PriorityLevel.P5_JOIN_LIKE

    def can_reply_to_user(self, user_id: str, *, is_donor: bool, now: float | None = None) -> bool:
        if is_donor:
            return True
        now_ts = time.monotonic() if now is None else now
        last = self._last_user_reply_at.get(user_id)
        if last is None:
            return True
        return (now_ts - last) >= self.user_cooldown_seconds

    def mark_user_replied(self, user_id: str, now: float | None = None) -> None:
        self._last_user_reply_at[user_id] = time.monotonic() if now is None else now

    def _is_duplicate_chat(self, event: NormalizedEvent, now: float) -> bool:
        if event.type != EventType.CHAT:
            return False
        text = (event.payload.text or "").strip().lower()
        key = (event.user.id, text)
        prev = self._recent_text_hashes.get(key)
        # Limpeza leve de entradas antigas (> 30s)
        if len(self._recent_text_hashes) > 1000:
            cutoff = now - 30.0
            self._recent_text_hashes = {
                k: v for k, v in self._recent_text_hashes.items() if v >= cutoff
            }
        if prev is not None and (now - prev) < 20.0:
            return True
        self._recent_text_hashes[key] = now
        return False

    def push(
        self,
        event: NormalizedEvent,
        *,
        is_donor: bool = False,
        now: float | None = None,
    ) -> bool:
        """Enfileira evento respeitando prioridade, dedup e capacidade."""
        now_ts = time.monotonic() if now is None else now
        prio = self.classify_priority(event)

        if prio == PriorityLevel.P5_JOIN_LIKE:
            if event.type == EventType.LIKE:
                self._like_total += event.payload.likes or 1
            # Entradas/curtidas não bloqueiam fila: só mantemos amostragem leve se fila vazia
            if len(self._heap) >= 10 or self.slow_mode:
                self.dropped_count += 1
                return False

        if prio == PriorityLevel.P3_FOLLOW_SHARE:
            self._follow_share_batch.append(event)
            return True

        if self._is_duplicate_chat(event, now_ts):
            self.dedup_dropped_count += 1
            return False

        if prio in (PriorityLevel.P2_MENTION_QUESTION, PriorityLevel.P4_CHAT) and (
            not self.can_reply_to_user(event.user.id, is_donor=is_donor, now=now_ts)
        ):
            self.dropped_count += 1
            return False

        if self.slow_mode and prio == PriorityLevel.P4_CHAT and len(self._heap) >= 5:
            self.dropped_count += 1
            return False

        item = QueuedItem(
            priority=int(prio),
            seq=next(self._counter),
            enqueued_at=now_ts,
            event=event,
            is_donor=is_donor,
        )
        heapq.heappush(self._heap, item)

        # Política de descarte: preserva P0/P1 e remove o item menos prioritário
        while len(self._heap) > self.max_size:
            worst_idx = max(
                range(len(self._heap)),
                key=lambda i: (self._heap[i].priority, self._heap[i].seq),
            )
            if self._heap[worst_idx].priority <= PriorityLevel.P1_GIFT:
                break
            self._heap.pop(worst_idx)
            heapq.heapify(self._heap)
            self.dropped_count += 1

        return True

    def pop_follow_share_batch(
        self, *, force: bool = False, now: float | None = None
    ) -> list[NormalizedEvent]:
        now_ts = time.monotonic() if now is None else now
        if not self._follow_share_batch:
            return []
        if not force and (now_ts - self._last_p3_flush) < self.p3_batch_interval_seconds:
            return []
        batch = list(self._follow_share_batch)
        self._follow_share_batch.clear()
        self._last_p3_flush = now_ts
        return batch

    def pop(self, now: float | None = None) -> QueuedItem | None:
        now_ts = time.monotonic() if now is None else now
        while self._heap:
            item = heapq.heappop(self._heap)
            if (
                item.priority == PriorityLevel.P2_MENTION_QUESTION
                and (now_ts - item.enqueued_at) > self.p2_ttl_seconds
            ):
                self.dropped_count += 1
                continue
            return item
        return None

    def clear_pending_speech_items(self) -> int:
        """Remove itens pendentes (usado pelo kill switch / veto)."""
        count = len(self._heap) + len(self._follow_share_batch)
        self._heap.clear()
        self._follow_share_batch.clear()
        return count

    @property
    def size(self) -> int:
        return len(self._heap)

    @property
    def total_likes(self) -> int:
        return self._like_total
