"""Métricas de latência, erros, FPS, custo e envio de alertas (Seção 12.2)."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


@dataclass
class MetricsRegistry:
    """Coleta métricas em janela móvel para painel, Prometheus e alertas."""

    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    llm_attempts: int = 0
    llm_errors: int = 0
    tts_attempts: int = 0
    tts_errors: int = 0
    total_events: int = 0
    moderated_blocked: int = 0
    tiktok_reconnects: int = 0
    godot_fps: float = 60.0
    godot_memory_mb: float = 0.0
    obs_dropped_frame_pct: float = 0.0
    alerts: deque[str] = field(default_factory=lambda: deque(maxlen=50))

    def record_latency(self, total_ms: float) -> None:
        self.latencies_ms.append(max(0.0, total_ms))

    def record_llm(self, *, failed: bool) -> None:
        self.llm_attempts += 1
        if failed:
            self.llm_errors += 1

    def record_tts(self, *, failed: bool) -> None:
        self.tts_attempts += 1
        if failed:
            self.tts_errors += 1

    def push_alert(self, message: str) -> None:
        logger.warning("ALERTA OPERACIONAL: %s", message)
        self.alerts.appendleft(message)

    @property
    def p50_latency_ms(self) -> float:
        return round(_percentile(list(self.latencies_ms), 50.0), 2)

    @property
    def p95_latency_ms(self) -> float:
        return round(_percentile(list(self.latencies_ms), 95.0), 2)

    @property
    def llm_error_rate(self) -> float:
        return round((self.llm_errors / self.llm_attempts), 4) if self.llm_attempts else 0.0

    @property
    def tts_error_rate(self) -> float:
        return round((self.tts_errors / self.tts_attempts), 4) if self.tts_attempts else 0.0

    def check_thresholds(
        self, *, queue_size: int, spent_usd: float, budget_usd: float
    ) -> list[str]:
        """Avalia limites da Seção 12.2."""
        fired: list[str] = []
        if self.p95_latency_ms > 10_000:
            fired.append(f"Latência p95 acima de 10 s ({self.p95_latency_ms:.0f} ms)")
        if self.llm_attempts >= 10 and self.llm_error_rate > 0.05:
            fired.append(f"Taxa de erro LLM acima de 5% ({self.llm_error_rate * 100:.1f}%)")
        if self.tts_attempts >= 10 and self.tts_error_rate > 0.05:
            fired.append(f"Taxa de erro TTS acima de 5% ({self.tts_error_rate * 100:.1f}%)")
        if self.godot_fps < 25.0 or self.obs_dropped_frame_pct > 2.0:
            fired.append(
                f"Queda de desempenho: FPS={self.godot_fps:.1f}, "
                f"perda OBS={self.obs_dropped_frame_pct:.1f}%"
            )
        if queue_size > 50:
            fired.append(f"Fila elevada ({queue_size} itens)")
        if budget_usd > 0 and (spent_usd / budget_usd) >= 0.8:
            fired.append(f"Orçamento diário >= 80% (${spent_usd:.2f}/${budget_usd:.2f})")
        for msg in fired:
            if not self.alerts or self.alerts[0] != msg:
                self.push_alert(msg)
        return fired

    def snapshot(
        self, *, queue_size: int, spent_usd: float, budget_usd: float
    ) -> dict[str, object]:
        return {
            "total_events": self.total_events,
            "moderated_blocked": self.moderated_blocked,
            "queue_size": queue_size,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "llm_error_rate": self.llm_error_rate,
            "tts_error_rate": self.tts_error_rate,
            "godot_fps": self.godot_fps,
            "godot_memory_mb": self.godot_memory_mb,
            "obs_dropped_frame_pct": self.obs_dropped_frame_pct,
            "tiktok_reconnects": self.tiktok_reconnects,
            "spent_usd": round(spent_usd, 4),
            "budget_usd": budget_usd,
            "alerts": list(self.alerts)[:10],
        }

    def to_prometheus(self, *, queue_size: int, spent_usd: float) -> str:
        lines = [
            f"live_events_total {self.total_events}",
            f"live_moderated_blocked_total {self.moderated_blocked}",
            f"live_queue_size {queue_size}",
            f"live_latency_p50_ms {self.p50_latency_ms}",
            f"live_latency_p95_ms {self.p95_latency_ms}",
            f"live_llm_error_rate {self.llm_error_rate}",
            f"live_tts_error_rate {self.tts_error_rate}",
            f"live_godot_fps {self.godot_fps}",
            f"live_spent_usd {spent_usd:.6f}",
        ]
        return "\n".join(lines) + "\n"


class AlertDispatcher:
    """Envia alertas operacionais por webhook (Discord / Slack / endpoint genérico)."""

    def __init__(self, webhook_url: str = "", *, timeout_seconds: float = 3.0) -> None:
        self.webhook_url = webhook_url.strip()
        self.timeout_seconds = timeout_seconds

    async def send(self, message: str) -> bool:
        if not self.webhook_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(
                    self.webhook_url,
                    json={"content": f"[Live IA TikTok] {message}", "text": message},
                )
                return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Falha ao enviar alerta para webhook: %s", exc)
            return False
