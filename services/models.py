"""Contratos de dados e modelos compartilhados entre ingestão, orquestrador e Godot."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Retorna timestamp UTC atual."""
    return datetime.now(UTC)


def new_event_id(prefix: str = "evt") -> str:
    """Gera identificador único legível."""
    return f"{prefix}_{uuid4().hex[:12]}"


class EventType(StrEnum):
    CHAT = "chat"
    GIFT = "gift"
    FOLLOW = "follow"
    SHARE = "share"
    LIKE = "like"
    JOIN = "join"
    VIEWER_COUNT = "viewer_count"
    OPERATOR = "operator"
    SYSTEM = "system"


class AvatarState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    REACTING = "REACTING"
    SAFE_MODE = "SAFE_MODE"


class Emotion(StrEnum):
    ALEGRE = "alegre"
    SURPRESA = "surpresa"
    TIMIDA = "timida"
    NEUTRA = "neutra"
    BRAVA = "brava"

    def to_godot(self) -> str:
        """Mapeia emoção em pt-BR para identificador esperado pela Godot."""
        mapping = {
            Emotion.ALEGRE: "happy",
            Emotion.SURPRESA: "surprised",
            Emotion.TIMIDA: "shy",
            Emotion.NEUTRA: "neutral",
            Emotion.BRAVA: "angry",
        }
        return mapping[self]


class PriorityLevel(IntEnum):
    P0_OPERATOR = 0
    P1_GIFT = 1
    P2_MENTION_QUESTION = 2
    P3_FOLLOW_SHARE = 3
    P4_CHAT = 4
    P5_JOIN_LIKE = 5


class GiftTierId(StrEnum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"


class EventUser(BaseModel):
    id: str = Field(min_length=1)
    nick: str = Field(min_length=1, max_length=80)
    is_follower: bool = False
    is_moderator: bool = False
    is_subscriber: bool = False


class GiftPayload(BaseModel):
    name: str = Field(min_length=1)
    coins: int = Field(ge=1)
    repeat: int = Field(default=1, ge=1)
    streak_final: bool = True
    gift_id: str | None = None


class EventPayload(BaseModel):
    text: str | None = None
    gift: GiftPayload | None = None
    likes: int | None = Field(default=None, ge=0)
    viewer_count: int | None = Field(default=None, ge=0)
    command: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedEvent(BaseModel):
    """Contrato 8.1: Evento normalizado de ingestão para o orquestrador."""

    id: str = Field(default_factory=lambda: new_event_id("evt"))
    ts: datetime = Field(default_factory=utc_now)
    type: EventType
    user: EventUser
    payload: EventPayload = Field(default_factory=EventPayload)


class VisemeFrame(BaseModel):
    t: float = Field(ge=0.0)
    v: str = Field(min_length=1)


class SpeakCommand(BaseModel):
    """Contrato 8.2: Comando speak para Godot."""

    cmd: Literal["speak"] = "speak"
    id: str = Field(default_factory=lambda: new_event_id("act"))
    priority: int = Field(default=2, ge=0, le=5)
    text: str
    audio_path: str
    visemes: list[VisemeFrame] = Field(default_factory=list)
    emotion: str = "neutral"
    gesture: str = "none"
    caption: bool = True
    interruptible: bool = True


class GiftFXCommand(BaseModel):
    """Contrato 8.2: Reação visual imediata a presente."""

    cmd: Literal["gift_fx"] = "gift_fx"
    gift: str
    tier: int = Field(ge=1, le=5)
    repeat: int = Field(default=1, ge=1)
    user: str
    fx: str = "particles_light"
    sound: str = "chime_short"
    gesture: str = "wave"


class OverlayCommand(BaseModel):
    """Contrato 8.2: Atualização de overlays."""

    cmd: Literal["overlay"] = "overlay"
    type: Literal["top_donors", "recent_gifts", "goal", "poll", "ai_badge", "hall_of_live"]
    data: Any


class SafeModeCommand(BaseModel):
    """Contrato 8.2: Ativação/desativação de modo seguro (kill switch)."""

    cmd: Literal["safe_mode"] = "safe_mode"
    on: bool
    reason: str | None = None


class StopSpeechCommand(BaseModel):
    """Comando para interromper imediatamente a fala atual na Godot."""

    cmd: Literal["stop_speech"] = "stop_speech"
    reason: str = "interrupted"


class SceneCommand(BaseModel):
    """Comando extra de cena/iluminação/cor/minijogo."""

    cmd: Literal["scene"] = "scene"
    scene_name: str | None = None
    mood: str | None = None
    color_hex: str | None = None
    action: str | None = None


class GodotInboundMessage(BaseModel):
    """Contrato 8.3: Mensagens recebidas da Godot."""

    type: Literal["speech_started", "speech_finished", "heartbeat", "error"]
    id: str | None = None
    fps: float | None = None
    memory_mb: float | None = None
    code: str | None = None
    context: str | None = None
    ts: datetime = Field(default_factory=utc_now)


class ModerationDecision(BaseModel):
    allowed: bool
    layer: Literal["rules", "classifier", "injection", "clean", "fallback"]
    reason: str | None = None
    sanitized_text: str | None = None


class LLMBrainOutput(BaseModel):
    """Saída JSON estruturada esperada do LLM (Seção 10.2)."""

    fala: str
    emocao: Emotion = Emotion.NEUTRA
    gesto: str = "none"


class PersonaProfile(BaseModel):
    name: str = "Luna"
    archetype: str = "gamer espacial curiosa, divertida e acolhedora"
    language: str = "pt-BR"
    tone: str = "Informal, animado, humor leve, pt-BR natural"
    max_sentences: int = Field(default=2, ge=1, le=5)
    max_words: int = Field(default=25, ge=5, le=80)
    mention_phrases: list[str] = Field(default_factory=lambda: ["luna", "@luna"])
    forbidden_topics: list[str] = Field(default_factory=list)
    fallback_lines: list[str] = Field(
        default_factory=lambda: [
            "Opa, deu uma interferência aqui! Manda de novo no chat!",
        ]
    )
    greetings_cache: list[str] = Field(default_factory=list)
    default_scene: str = "studio_space"
    safe_scene: str = "safe_mode"


class GiftTierConfig(BaseModel):
    id: GiftTierId
    name: str
    min_coins: int = Field(ge=1)
    max_coins: int = Field(ge=1)
    fx: str
    sound: str
    gesture: str
    emotion: Emotion = Emotion.ALEGRE
    interrupt: bool = False
    use_llm: bool = True
    cache_template: str | None = None
    overlay_highlight_seconds: int = 0
    top_donor: bool = False
    hall_of_live: bool = False
    alert_operator: bool = False

    @property
    def tier_number(self) -> int:
        return int(self.id.value[1:])


class LiveGoalConfig(BaseModel):
    title: str = "Meta da Live"
    target_coins: int = Field(default=5000, ge=1)
    reward_fx: str = "confetti_supernova"


class GiftMatrixConfig(BaseModel):
    tiers: list[GiftTierConfig]
    combo_steps: list[int] = Field(default_factory=lambda: [5, 10, 50, 100])
    live_goal: LiveGoalConfig = Field(default_factory=LiveGoalConfig)
    custom_gifts: dict[str, dict[str, str]] = Field(default_factory=dict)


class IdleTopic(BaseModel):
    id: str
    weight: int = Field(default=1, ge=1)
    category: str = "geral"
    text: str


class IdleConfig(BaseModel):
    idle_interval_seconds: float = Field(default=15.0, ge=1.0)
    topics: list[IdleTopic] = Field(default_factory=list)
