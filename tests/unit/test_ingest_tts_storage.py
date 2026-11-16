"""Testes unitários de ingestão, TTS/cache/visemes e persistência SQLite/LGPD."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.ingest.base import LiveSource
from services.ingest.simulator_source import SimulatorSource
from services.ingest.tiktoklive_source import TikTokLiveSource
from services.models import (
    Emotion,
    EventPayload,
    EventType,
    EventUser,
    GiftPayload,
    NormalizedEvent,
)
from services.orchestrator.storage import AuditRepository
from services.orchestrator.voice.cache import AudioCache
from services.orchestrator.voice.tts_client import MockSineTTSClient, TTSClient, VoiceService


class _FailingTTSClient(TTSClient):
    async def synthesize(self, *, text, voice, emotion, timeout_seconds):
        raise TimeoutError("TTS timeout simulado")


class _DummySource(LiveSource):
    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._closed = True
        self._connected = False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_live_source_backoff_and_streak_aggregation():
    src = _DummySource(base_backoff_seconds=1.0, max_backoff_seconds=60.0)
    src._consecutive_failures = 1
    assert src.next_backoff() == 1.0
    src._consecutive_failures = 4
    assert src.next_backoff() == 8.0
    src._consecutive_failures = 10
    assert src.next_backoff() == 60.0

    received: list[NormalizedEvent] = []
    src.on_event(lambda ev: received.append(ev))
    user = EventUser(id="u1", nick="ComboUser")
    await src.emit_event(
        NormalizedEvent(
            type=EventType.GIFT,
            user=user,
            payload=EventPayload(
                gift=GiftPayload(name="Rose", coins=3, repeat=3, streak_final=False)
            ),
        )
    )
    assert len(received) == 0
    await src.emit_event(
        NormalizedEvent(
            type=EventType.GIFT,
            user=user,
            payload=EventPayload(
                gift=GiftPayload(name="Rose", coins=5, repeat=5, streak_final=True)
            ),
        )
    )
    assert len(received) == 1
    assert received[0].payload.gift is not None
    assert received[0].payload.gift.repeat == 5


@pytest.mark.unit
def test_tiktoklive_normalization_without_network():
    raw_user = SimpleNamespace(
        id=12345, nickname="Carlos", follow_info=SimpleNamespace(follow_status=1)
    )
    ev_chat = TikTokLiveSource.normalize_raw_event(
        "chat", SimpleNamespace(user=raw_user, comment="Oi Luna!")
    )
    assert ev_chat.type == EventType.CHAT
    assert ev_chat.user.nick == "Carlos"
    assert ev_chat.user.is_follower is True

    ev_gift = TikTokLiveSource.normalize_raw_event(
        "gift",
        SimpleNamespace(
            user=raw_user,
            gift=SimpleNamespace(id=99, name="Lion", diamond_count=500, streakable=True),
            repeat_count=2,
            streaking=False,
        ),
    )
    assert ev_gift.type == EventType.GIFT
    assert ev_gift.payload.gift is not None
    assert ev_gift.payload.gift.coins == 1000
    assert ev_gift.payload.gift.streak_final is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulator_source_emits_scripted_events():
    ev = NormalizedEvent(
        type=EventType.CHAT,
        user=EventUser(id="99", nick="Tester"),
        payload=EventPayload(text="Teste simulador"),
    )
    sim = SimulatorSource(events_per_second=100.0, scripted_events=[ev])
    out: list[NormalizedEvent] = []
    sim.on_event(lambda e: out.append(e))
    await sim.connect()
    assert len(out) == 1
    assert out[0].payload.text == "Teste simulador"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_service_cache_and_fallback(tmp_path: Path):
    voice = VoiceService(
        client=MockSineTTSClient(),
        cache=AudioCache(tmp_path / "cache"),
        voice="pt-BR-FranciscaNeural",
    )
    r1 = await voice.synthesize_speech("Oiê tripulação, tudo bem?", Emotion.ALEGRE)
    assert r1.cache_hit is False
    assert r1.audio_path.exists()
    assert r1.audio_path.read_bytes()[:4] == b"RIFF"
    assert len(r1.visemes) >= 2

    r2 = await voice.synthesize_speech("Oiê tripulação, tudo bem?", Emotion.ALEGRE)
    assert r2.cache_hit is True
    assert r2.audio_path == r1.audio_path

    # Fallback quando TTS falha
    failing_voice = VoiceService(
        client=_FailingTTSClient(),
        cache=AudioCache(tmp_path / "cache_fail"),
    )
    rf = await failing_voice.synthesize_speech("Qualquer fala", Emotion.NEUTRA)
    assert rf.used_fallback is True
    assert rf.audio_path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_audit_and_lgpd_retention(tmp_path: Path):
    repo = AuditRepository(tmp_path / "audit.db")
    await repo.initialize()
    await repo.start_session("sess_1")

    old_ts = datetime.now(UTC) - timedelta(days=45)
    old_ev = NormalizedEvent(
        id="evt_old",
        ts=old_ts,
        type=EventType.CHAT,
        user=EventUser(id="u_old", nick="Antigo"),
        payload=EventPayload(text="mensagem antiga"),
    )
    new_ev = NormalizedEvent(
        id="evt_new",
        type=EventType.CHAT,
        user=EventUser(id="u_new", nick="Novo"),
        payload=EventPayload(text="mensagem nova"),
    )
    await repo.upsert_user(user_id="u_new", nick="Novo", coins_delta=50)
    await repo.log_event(old_ev, "allowed")
    await repo.log_event(new_ev, "allowed")
    await repo.log_utterance(
        utterance_id="act_1",
        event_id="evt_new",
        text="Oi Novo!",
        emotion="alegre",
        llm_ms=10.0,
        tts_ms=5.0,
        total_ms=15.0,
        cost_estimate=0.0001,
    )

    purged = await repo.purge_expired(retention_days=30)
    assert purged >= 1
    events = await repo.recent_events()
    assert len(events) == 1
    assert events[0]["id"] == "evt_new"

    deleted = await repo.delete_user_data("u_new")
    assert deleted >= 2
    await repo.end_session("sess_1", peak_viewers=100, total_coins=50, total_events=2)
    await repo.close()
