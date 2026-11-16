"""Testes offline de hardening: sync de fala, auto slow-mode, webhook, prewarm, reconnect e soak."""

from __future__ import annotations

import asyncio
import json
import tracemalloc
from pathlib import Path

import httpx
import pytest
import respx
from websockets.asyncio.client import connect as ws_connect

from services.ingest.base import LiveSource
from services.ingest.tiktoklive_source import TikTokLiveSource
from services.models import (
    Emotion,
    EventPayload,
    EventType,
    EventUser,
    GiftPayload,
    NormalizedEvent,
)
from services.orchestrator.config import Settings
from services.orchestrator.metrics import AlertDispatcher
from services.orchestrator.pipeline import OrchestratorService
from services.orchestrator.queue import EventPriorityQueue
from services.orchestrator.voice.cache import AudioCache
from services.orchestrator.voice.tts_client import MockSineTTSClient, VoiceService


class _FlakySource(LiveSource):
    def __init__(self) -> None:
        super().__init__(
            base_backoff_seconds=0.005, max_backoff_seconds=0.02, alert_after_failures=3
        )
        self.attempts = 0

    async def connect(self) -> None:
        self.attempts += 1
        if self.attempts <= 3:
            raise ConnectionError(f"falha #{self.attempts}")
        self._connected = True
        self._closed = True

    async def close(self) -> None:
        self._closed = True
        self._connected = False


@pytest.mark.unit
def test_queue_auto_raid_slow_mode():
    q = EventPriorityQueue()
    for i in range(130):
        q.record_arrival(now=100.0 + (i * 0.01))
    changed, rate = q.evaluate_raid_protection(now=102.0)
    assert changed is True
    assert q.slow_mode is True
    assert rate >= 25.0

    # Após 6s sem tráfego, taxa cai para 0 -> slow_mode desliga
    changed_off, rate_off = q.evaluate_raid_protection(now=110.0)
    assert changed_off is True
    assert q.slow_mode is False
    assert rate_off == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alert_dispatcher_and_voice_prewarm(tmp_path: Path):
    dispatcher = AlertDispatcher("https://hooks.example.test/alerts")
    with respx.mock() as router:
        route = router.post("https://hooks.example.test/alerts").mock(
            return_value=httpx.Response(204)
        )
        ok = await dispatcher.send("Teste de alerta")
        assert ok is True
        assert route.called

    voice = VoiceService(client=MockSineTTSClient(), cache=AudioCache(tmp_path / "cache"))
    warmed = await voice.prewarm_phrases(["Oiê tripulação!", "Já deixa o like!"])
    assert warmed == 2
    warmed_again = await voice.prewarm_phrases(["Oiê tripulação!", "Já deixa o like!"])
    assert warmed_again == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_alert_after_three_failures_and_tiktok_mock():
    alerts: list[str] = []
    src = _FlakySource()
    src.on_alert(lambda m: alerts.append(m))
    await src.run_with_reconnect()
    assert src.attempts == 4
    assert len(alerts) == 1

    # TikTokLiveSource com client_factory mockada
    state = {"started": False, "stopped": False}

    class _FakeTTClient:
        def on(self, _cls):
            return lambda fn: fn

        async def start(self):
            state["started"] = True

        async def disconnect(self):
            state["stopped"] = True

    tt = TikTokLiveSource("luna_oficial", client_factory=lambda uid: _FakeTTClient())
    assert tt.unique_id == "@luna_oficial"
    await tt.connect()
    await tt.close()
    assert state == {"started": True, "stopped": True}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_speech_sync_urgent_interrupt_and_veto(tmp_path: Path):
    settings = Settings(
        config_dir=Path("config"),
        sqlite_path=tmp_path / "sync.db",
        tts_cache_dir=tmp_path / "cache",
        ws_host="127.0.0.1",
        ws_port=0,
    )
    orch = OrchestratorService(settings)
    await orch.start(start_ws=True, start_source=False)
    port = orch.ws_server.bound_port

    async with ws_connect(f"ws://127.0.0.1:{port}/avatar") as ws:
        # Aguarda registro do cliente WS
        await asyncio.sleep(0.05)

        await orch.inject_speech("Primeira fala longa", Emotion.ALEGRE)
        speak_msg = None
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            if data.get("cmd") == "speak":
                speak_msg = data
                break
        assert speak_msg is not None
        assert orch._speech_done_event.is_set() is False

        # Veto da fala atual envia stop_speech e libera o lock
        await orch.veto_utterance("current")
        assert orch._speech_done_event.is_set() is True

        stop_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
        assert stop_msg["cmd"] == "stop_speech"

        # Presente épico T4 envia interrupt=True e completa speech_finished
        await orch.ingest_event(
            NormalizedEvent(
                type=EventType.GIFT,
                user=EventUser(id="vip_1", nick="SuperDoador"),
                payload=EventPayload(gift=GiftPayload(name="Galaxy", coins=1000, repeat=1)),
            )
        )
        await asyncio.sleep(0.2)
        if orch.state_machine.current_utterance_id:
            await ws.send(
                json.dumps(
                    {
                        "type": "speech_finished",
                        "id": orch.state_machine.current_utterance_id,
                    }
                )
            )
            await asyncio.sleep(0.05)

    await orch.stop()


@pytest.mark.load
@pytest.mark.asyncio
async def test_memory_stability_soak_1000_events(tmp_path: Path):
    """Verifica ausência de vazamento significativo de memória em 1.000 ciclos (CA-10)."""
    settings = Settings(
        config_dir=Path("config"),
        sqlite_path=tmp_path / "soak.db",
        tts_cache_dir=tmp_path / "cache",
    )
    orch = OrchestratorService(settings)
    await orch.audit.initialize()
    await orch.audit.start_session(orch.session_id)

    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    for i in range(1000):
        await orch.ingest_event(
            NormalizedEvent(
                type=EventType.CHAT,
                user=EventUser(id=f"soak_{i % 50}", nick=f"Soak{i % 50}"),
                payload=EventPayload(text=f"Mensagem soak {i}"),
            )
        )
        if i % 20 == 0:
            await orch.process_next_event()

    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    diff_kb = sum(stat.size_diff for stat in snap_after.compare_to(snap_before, "lineno")) / 1024.0
    assert diff_kb < 5120.0, f"Crescimento de memória inesperado: {diff_kb:.1f} KB"
    await orch.audit.close()
