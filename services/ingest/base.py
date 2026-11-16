"""Interface LiveSource, backoff exponencial e controle de combos/streaks."""

from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from services.models import NormalizedEvent

logger = logging.getLogger(__name__)

EventCallback = Callable[[NormalizedEvent], Awaitable[None] | None]
SourceAlertCallback = Callable[[str], Awaitable[None] | None]


class LiveSource(ABC):
    """Interface única de ingestão (Seção 7.1): connect(), on_event(cb), close()."""

    def __init__(
        self,
        *,
        max_backoff_seconds: float = 60.0,
        base_backoff_seconds: float = 1.0,
        alert_after_failures: int = 3,
    ) -> None:
        self._callbacks: list[EventCallback] = []
        self._alert_callbacks: list[SourceAlertCallback] = []
        self._max_backoff_seconds = max_backoff_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._alert_after_failures = alert_after_failures
        self._connected = False
        self._consecutive_failures = 0
        self._reconnect_count = 0
        self._closed = False
        self._pending_streaks: dict[tuple[str, str], NormalizedEvent] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def on_event(self, cb: EventCallback) -> None:
        """Registra consumidor de eventos normalizados."""
        self._callbacks.append(cb)

    def on_alert(self, cb: SourceAlertCallback) -> None:
        """Registra callback de alerta operacional (ex: 3 falhas seguidas)."""
        self._alert_callbacks.append(cb)

    async def emit_event(self, event: NormalizedEvent) -> None:
        """Entrega evento normalizado (com agregação de streak de presentes)."""
        if event.payload.gift is not None and not event.payload.gift.streak_final:
            key = (event.user.id, event.payload.gift.name)
            self._pending_streaks[key] = event
            return

        if event.payload.gift is not None and event.payload.gift.streak_final:
            key = (event.user.id, event.payload.gift.name)
            self._pending_streaks.pop(key, None)

        for cb in list(self._callbacks):
            res = cb(event)
            if inspect.isawaitable(res):
                await res

    async def flush_pending_streaks(self) -> None:
        """Finaliza combos pendentes no encerramento de sessão."""
        for key in list(self._pending_streaks.keys()):
            ev = self._pending_streaks.pop(key)
            if ev.payload.gift is not None:
                ev.payload.gift.streak_final = True
            await self.emit_event(ev)

    async def _emit_alert(self, message: str) -> None:
        logger.warning("LiveSource alert: %s", message)
        for cb in list(self._alert_callbacks):
            res = cb(message)
            if inspect.isawaitable(res):
                await res

    def next_backoff(self) -> float:
        """Calcula backoff exponencial (1, 2, 4, 8 ... até 60 s)."""
        delay = self._base_backoff_seconds * (2 ** max(0, self._consecutive_failures - 1))
        return float(min(delay, self._max_backoff_seconds))

    async def run_with_reconnect(self) -> None:
        """Conecta e reconecta automaticamente com backoff exponencial até close()."""
        self._closed = False
        while not self._closed:
            try:
                await self.connect()
                self._consecutive_failures = 0
                if self._closed:
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._consecutive_failures += 1
                self._reconnect_count += 1
                if self._consecutive_failures >= self._alert_after_failures:
                    await self._emit_alert(
                        f"Falha na conexão da live ({self._consecutive_failures}x): {exc}"
                    )
                if self._closed:
                    break
                delay = self.next_backoff()
                await asyncio.sleep(delay)
            else:
                # Conexão encerrou sem exceção (ex: fim de stream); reconecta se não fechado
                if not self._closed:
                    self._consecutive_failures += 1
                    self._reconnect_count += 1
                    await asyncio.sleep(self.next_backoff())

    @abstractmethod
    async def connect(self) -> None:
        """Abre a conexão e inicia leitura até desconexão ou close()."""

    @abstractmethod
    async def close(self) -> None:
        """Encerra a conexão com segurança."""
