"""Testes de integração ponta a ponta (WS Godot, FastAPI operador, 1.000 gifts, kill switch)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.client import connect as ws_connect

from services.models import (
    Emotion,
    EventPayload,
    EventType,
    EventUser,
    GiftPayload,
    LLMBrainOutput,
    NormalizedEvent,
)
from services.orchestrator.api import create_operator_app
from services.orchestrator.brain.llm_client import LLMClient
from services.orchestrator.config import Settings
from services.orchestrator.pipeline import OrchestratorService


class _SlowTimeoutLLM(LLMClient):
    async def generate_json(self, *, system_prompt, user_prompt, model, timeout_seconds):
        await asyncio.sleep(1.5)
        return LLMBrainOutput(fala="Demorou", emocao=Emotion.NEUTRA), 1, 1


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        config_dir=Path("config"),
        sqlite_path=tmp_path / "live.db",
        tts_cache_dir=tmp_path / "audio_cache",
        ws_host="127.0.0.1",
        ws_port=0,  # porta efêmera livre
        llm_timeout_seconds=0.5,
        tts_timeout_seconds=1.0,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_ws_godot_gift_chat_and_kill_switch(tmp_path: Path):
    orch = OrchestratorService(_make_settings(tmp_path))
    await orch.start(start_ws=True, start_source=False)
    port = orch.ws_server.bound_port

    received_cmds: list[dict] = []

    async with ws_connect(f"ws://127.0.0.1:{port}/avatar") as ws:
        # Envia heartbeat Godot
        await ws.send(json.dumps({"type": "heartbeat", "fps": 59.5, "memory_mb": 140.0}))

        # Injeta presente T2 e chat comum
        await orch.ingest_event(
            NormalizedEvent(
                type=EventType.GIFT,
                user=EventUser(id="u_gift", nick="Marina"),
                payload=EventPayload(gift=GiftPayload(name="Perfume", coins=20, repeat=1)),
            )
        )
        await orch.ingest_event(
            NormalizedEvent(
                type=EventType.CHAT,
                user=EventUser(id="u_chat", nick="Lucas"),
                payload=EventPayload(text="Oi Luna, qual seu planeta favorito?"),
            )
        )

        # Aguarda mensagens WebSocket (gift_fx + overlays + speaks)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(received_cmds) < 5:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                received_cmds.append(json.loads(raw))
            except TimeoutError:
                pass

        cmds_types = {c["cmd"] for c in received_cmds}
        assert "gift_fx" in cmds_types
        assert "overlay" in cmds_types
        assert "speak" in cmds_types

        # Verifica kill switch <= 2s (CA-12)
        t0 = time.perf_counter()
        await orch.activate_kill_switch("teste_ca12")
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0
        assert orch.state_machine.safe_mode is True

        # Procura comando safe_mode entregue à Godot
        safe_msgs: list[dict] = []
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("cmd") == "safe_mode":
                    safe_msgs.append(msg)
                    break
            except TimeoutError:
                break
        assert any(m.get("on") is True for m in safe_msgs)

    assert orch.metrics.godot_fps == pytest.approx(59.5)
    await orch.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gift_integrity_1000_gifts_zero_lost_zero_duplicated(tmp_path: Path):
    """CA-04: 1.000 presentes simulados -> 0 perdidos, 0 duplicados."""
    orch = OrchestratorService(_make_settings(tmp_path))
    await orch.audit.initialize()
    await orch.audit.start_session(orch.session_id)

    for i in range(1000):
        await orch.ingest_event(
            NormalizedEvent(
                id=f"gift_{i}",
                type=EventType.GIFT,
                user=EventUser(id=f"d_{i % 20}", nick=f"Doador{i % 20}"),
                payload=EventPayload(gift=GiftPayload(name="Rose", coins=2, repeat=1)),
            )
        )

    assert orch.memory.total_coins == 2000
    assert orch.metrics.total_events == 1000
    await orch.audit.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_timeout_triggers_fallback_under_3s(tmp_path: Path):
    """CA-13: Com LLM lento/indisponível, fala de contingência <= 3s."""
    orch = OrchestratorService(_make_settings(tmp_path), llm_client=_SlowTimeoutLLM())
    await orch.audit.initialize()
    await orch.audit.start_session(orch.session_id)

    await orch.ingest_event(
        NormalizedEvent(
            type=EventType.CHAT,
            user=EventUser(id="u_fb", nick="Pedro"),
            payload=EventPayload(text="Oi Luna!"),
        )
    )
    t0 = time.perf_counter()
    processed = await orch.process_next_event()
    elapsed = time.perf_counter() - t0
    assert processed is True
    assert elapsed < 3.0
    assert orch.metrics.llm_errors == 1
    await orch.audit.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_operator_fastapi_endpoints_and_auth(tmp_path: Path):
    settings = _make_settings(tmp_path)
    settings.operator_token = "secret-op-token"
    orch = OrchestratorService(settings)
    await orch.audit.initialize()
    await orch.audit.start_session(orch.session_id)
    app = create_operator_app(orch)

    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 401

    headers = {"Authorization": "Bearer secret-op-token"}
    assert client.get("/api/status", headers=headers).status_code == 200
    assert client.post("/api/pause", headers=headers).json()["paused"] is True
    assert client.post("/api/resume", headers=headers).json()["paused"] is False
    assert (
        client.post(
            "/api/inject-speech",
            headers=headers,
            json={"text": "Olá pessoal!", "emotion": "alegre"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/mood-scene", headers=headers, json={"mood": "surpresa", "scene": "neon_arcade"}
        ).status_code
        == 200
    )
    assert (
        client.post("/api/banned-terms", headers=headers, json={"term": "spam_xyz"}).status_code
        == 200
    )
    assert client.delete("/api/banned-terms/spam_xyz", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/ban", headers=headers, json={"user_id": "bad_1", "nick": "Bad"}
        ).status_code
        == 200
    )
    assert client.post("/api/veto/act_999", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/simulate/chat",
            headers=headers,
            json={"user_id": "sim1", "nick": "Sim", "text": "Tudo bem?"},
        ).status_code
        == 200
    )
    assert client.post("/api/kill-switch", headers=headers).json()["safe_mode"] is True
    assert client.post("/api/safe-mode/off", headers=headers).json()["safe_mode"] is False
    assert client.delete("/api/users/bad_1", headers=headers).status_code == 200
    assert "live_events_total" in client.get("/metrics").text
    await orch.audit.close()
