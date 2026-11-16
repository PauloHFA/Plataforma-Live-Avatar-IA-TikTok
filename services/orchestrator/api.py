"""API FastAPI e Painel Web do Operador (Seção 12.1, RF-15, CA-18)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from services.models import Emotion, EventPayload, EventType, EventUser, NormalizedEvent
from services.orchestrator.pipeline import OrchestratorService

PANEL_HTML_PATH = Path(__file__).resolve().parent.parent / "operator-panel" / "index.html"


class InjectSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    emotion: Emotion = Emotion.ALEGRE


class BanUserRequest(BaseModel):
    user_id: str = Field(min_length=1)
    nick: str = "Banido"


class BannedTermRequest(BaseModel):
    term: str = Field(min_length=1, max_length=80)


class MoodSceneRequest(BaseModel):
    mood: Emotion | None = None
    scene: str | None = None


class SimulatedChatRequest(BaseModel):
    user_id: str = "sim_user_1"
    nick: str = "Visitante"
    text: str = Field(min_length=1, max_length=300)


def create_operator_app(orchestrator: OrchestratorService) -> FastAPI:
    app = FastAPI(title="Live IA TikTok - Painel do Operador", version="0.1.0")

    def require_operator_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = orchestrator.settings.operator_token.strip()
        if not expected or expected == "[REDACTED]":
            return
        if authorization != f"Bearer {expected}":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de operador inválido ou ausente",
            )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "avatar_state": orchestrator.state_machine.state.value,
            "safe_mode": orchestrator.state_machine.safe_mode,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        if PANEL_HTML_PATH.exists():
            return PANEL_HTML_PATH.read_text(encoding="utf-8")
        return "<h1>Painel do Operador - Live IA TikTok</h1>"

    @app.get("/api/status", dependencies=[Depends(require_operator_auth)])
    async def get_status() -> dict[str, Any]:
        sm = orchestrator.state_machine
        return {
            "avatar_state": sm.state.value,
            "paused": sm.paused,
            "safe_mode": sm.safe_mode,
            "economy_mode": sm.economy_mode,
            "mood": sm.mood.value,
            "scene": sm.scene,
            "connections": {
                "tiktok": orchestrator.source.is_connected if orchestrator.source else False,
                "godot_clients": orchestrator.ws_server.client_count,
                "obs": orchestrator.obs.connected,
            },
            "metrics": orchestrator.metrics.snapshot(
                queue_size=orchestrator.queue.size,
                spent_usd=sm.spent_usd,
                budget_usd=sm.daily_budget_usd,
            ),
            "total_coins": orchestrator.memory.total_coins,
            "top_donors": orchestrator.memory.top_donors(),
            "banned_terms": sorted(orchestrator.moderation.rules.banned_terms),
        }

    @app.get("/api/events", dependencies=[Depends(require_operator_auth)])
    async def get_events() -> dict[str, Any]:
        return {"items": list(orchestrator.live_feed)}

    @app.post("/api/kill-switch", dependencies=[Depends(require_operator_auth)])
    async def kill_switch() -> dict[str, Any]:
        await orchestrator.activate_kill_switch("operator_panel")
        return {"safe_mode": True, "state": orchestrator.state_machine.state.value}

    @app.post("/api/safe-mode/off", dependencies=[Depends(require_operator_auth)])
    async def safe_mode_off() -> dict[str, Any]:
        await orchestrator.resume_from_safe_mode()
        return {"safe_mode": False, "state": orchestrator.state_machine.state.value}

    @app.post("/api/pause", dependencies=[Depends(require_operator_auth)])
    async def pause() -> dict[str, Any]:
        await orchestrator.pause()
        return {"paused": True}

    @app.post("/api/resume", dependencies=[Depends(require_operator_auth)])
    async def resume() -> dict[str, Any]:
        await orchestrator.resume()
        return {"paused": False, "safe_mode": orchestrator.state_machine.safe_mode}

    @app.post("/api/inject-speech", dependencies=[Depends(require_operator_auth)])
    async def inject_speech(body: InjectSpeechRequest) -> dict[str, Any]:
        await orchestrator.inject_speech(body.text, emotion=body.emotion)
        return {"status": "spoken", "text": body.text}

    @app.post("/api/mood-scene", dependencies=[Depends(require_operator_auth)])
    async def set_mood_scene(body: MoodSceneRequest) -> dict[str, Any]:
        await orchestrator.set_mood_and_scene(mood=body.mood, scene=body.scene)
        return {
            "mood": orchestrator.state_machine.mood.value,
            "scene": orchestrator.state_machine.scene,
        }

    @app.post("/api/ban", dependencies=[Depends(require_operator_auth)])
    async def ban_user(body: BanUserRequest) -> dict[str, Any]:
        await orchestrator.ban_user(body.user_id, body.nick)
        return {"banned": body.user_id}

    @app.post("/api/banned-terms", dependencies=[Depends(require_operator_auth)])
    async def add_banned_term(body: BannedTermRequest) -> dict[str, Any]:
        orchestrator.moderation.rules.add_banned_term(body.term)
        return {"banned_terms": sorted(orchestrator.moderation.rules.banned_terms)}

    @app.delete("/api/banned-terms/{term}", dependencies=[Depends(require_operator_auth)])
    async def remove_banned_term(term: str) -> dict[str, Any]:
        orchestrator.moderation.rules.remove_banned_term(term)
        return {"banned_terms": sorted(orchestrator.moderation.rules.banned_terms)}

    @app.post("/api/veto/{utterance_id}", dependencies=[Depends(require_operator_auth)])
    async def veto_utterance(utterance_id: str) -> dict[str, Any]:
        orchestrator.vetoed_utterances.add(utterance_id)
        cleared = orchestrator.queue.clear_pending_speech_items()
        return {"vetoed": utterance_id, "cleared_queue": cleared}

    @app.post("/api/simulate/chat", dependencies=[Depends(require_operator_auth)])
    async def simulate_chat(body: SimulatedChatRequest) -> dict[str, Any]:
        ev = NormalizedEvent(
            type=EventType.CHAT,
            user=EventUser(id=body.user_id, nick=body.nick),
            payload=EventPayload(text=body.text),
        )
        await orchestrator.ingest_event(ev)
        return {"queued_id": ev.id}

    @app.delete("/api/users/{user_id}", dependencies=[Depends(require_operator_auth)])
    async def lgpd_delete_user(user_id: str) -> dict[str, Any]:
        deleted = await orchestrator.audit.delete_user_data(user_id)
        return {"deleted_rows": deleted, "user_id": user_id}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics() -> str:
        return orchestrator.metrics.to_prometheus(
            queue_size=orchestrator.queue.size,
            spent_usd=orchestrator.state_machine.spent_usd,
        )

    return app
