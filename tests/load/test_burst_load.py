"""Teste de carga e rajada (CA-07): 50+ eventos/s sem travamento ou perda de presentes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from services.models import EventPayload, EventType, EventUser, GiftPayload, NormalizedEvent
from services.orchestrator.config import Settings
from services.orchestrator.pipeline import OrchestratorService


@pytest.mark.load
@pytest.mark.asyncio
async def test_burst_500_events_maintains_bounds_and_gifts(tmp_path: Path):
    settings = Settings(
        config_dir=Path("config"),
        sqlite_path=tmp_path / "load.db",
        tts_cache_dir=tmp_path / "cache",
    )
    orch = OrchestratorService(settings)
    await orch.audit.initialize()
    await orch.audit.start_session(orch.session_id)

    t0 = time.perf_counter()
    for i in range(500):
        if i % 25 == 0:
            ev = NormalizedEvent(
                type=EventType.GIFT,
                user=EventUser(id=f"vip_{i}", nick=f"VIP{i}"),
                payload=EventPayload(gift=GiftPayload(name="Rose", coins=5, repeat=1)),
            )
        else:
            ev = NormalizedEvent(
                type=EventType.CHAT,
                user=EventUser(id=f"u_{i}", nick=f"User{i}"),
                payload=EventPayload(text=f"Comentário rajada número {i}"),
            )
        await orch.ingest_event(ev)
    elapsed = time.perf_counter() - t0

    assert orch.metrics.total_events == 500
    assert orch.queue.size <= orch.queue.max_size
    assert orch.memory.total_coins == 100  # 20 gifts * 5 coins
    assert elapsed < 5.0

    # Processa alguns itens de alta prioridade primeiro -> presentes (P1)
    first_processed = await orch.process_next_event()
    assert first_processed is True
    await orch.audit.close()
