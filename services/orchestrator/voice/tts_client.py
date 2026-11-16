"""Cliente TTS desacoplado com cache, timeout (<=4s) e contingência."""

from __future__ import annotations

import asyncio
import html
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx

from services.models import Emotion, VisemeFrame
from services.orchestrator.voice.cache import AudioCache
from services.orchestrator.voice.visemes import (
    generate_visemes_from_text,
    synthesize_sine_wav,
)

logger = logging.getLogger(__name__)


@dataclass
class TTSResult:
    audio_path: Path
    visemes: list[VisemeFrame]
    cache_hit: bool
    used_fallback: bool
    latency_ms: float
    char_count: int


class TTSClient(ABC):
    @abstractmethod
    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        emotion: Emotion,
        timeout_seconds: float,
    ) -> tuple[bytes, list[VisemeFrame]]:
        """Retorna (bytes WAV, timeline de visemes)."""


class MockSineTTSClient(TTSClient):
    """Sintetizador local leve para testes e simulação."""

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        emotion: Emotion,
        timeout_seconds: float,
    ) -> tuple[bytes, list[VisemeFrame]]:
        del voice, emotion, timeout_seconds
        wav_bytes, duration = synthesize_sine_wav(text)
        visemes = generate_visemes_from_text(text, duration)
        return wav_bytes, visemes


class AzureSpeechTTSClient(TTSClient):
    """Cliente REST Azure Speech (pt-BR neural + SSML)."""

    def __init__(self, *, api_key: str, region: str) -> None:
        self.api_key = api_key
        self.region = region
        self._client = httpx.AsyncClient()

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        emotion: Emotion,
        timeout_seconds: float,
    ) -> tuple[bytes, list[VisemeFrame]]:
        if not self.api_key:
            raise RuntimeError("TTS_API_KEY não configurada para Azure Speech")

        safe_text = html.escape(text)
        pitch = "+6%" if emotion in (Emotion.ALEGRE, Emotion.SURPRESA) else "+0%"
        ssml = (
            f"<speak version='1.0' xml:lang='pt-BR'>"
            f"<voice name='{voice}'><prosody pitch='{pitch}'>{safe_text}</prosody></voice>"
            f"</speak>"
        )
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        resp = await self._client.post(
            url,
            headers={
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
            },
            content=ssml.encode("utf-8"),
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        wav_bytes = resp.content
        duration = max(0.5, len(wav_bytes) / (16000 * 2))
        visemes = generate_visemes_from_text(text, duration)
        return wav_bytes, visemes

    async def close(self) -> None:
        await self._client.aclose()


class VoiceService:
    """Orquestra síntese de voz com cache e arquivo de contingência (RF-16, CA-13)."""

    def __init__(
        self,
        *,
        client: TTSClient,
        cache: AudioCache,
        voice: str = "pt-BR-FranciscaNeural",
        timeout_seconds: float = 4.0,
    ) -> None:
        self.client = client
        self.cache = cache
        self.voice = voice
        self.timeout_seconds = timeout_seconds
        self._fallback_text = "Opa, deu uma interferência aqui na nave!"

    def _ensure_fallback_audio(self) -> tuple[Path, list[VisemeFrame]]:
        cached = self.cache.get(self.voice, self._fallback_text)
        if cached is not None:
            return cached
        wav_bytes, dur = synthesize_sine_wav(self._fallback_text)
        visemes = generate_visemes_from_text(self._fallback_text, dur)
        path = self.cache.put(self.voice, self._fallback_text, wav_bytes, visemes)
        return path, visemes

    async def prewarm_phrases(
        self, phrases: list[str], *, emotion: Emotion = Emotion.ALEGRE
    ) -> int:
        """Pré-aquece saudações e falas de contingência no startup."""
        self._ensure_fallback_audio()
        warmed = 0
        for phrase in phrases:
            cleaned = phrase.strip()
            if not cleaned:
                continue
            if self.cache.get(self.voice, cleaned) is None:
                await self.synthesize_speech(cleaned, emotion=emotion)
                warmed += 1
        return warmed

    async def synthesize_speech(self, text: str, emotion: Emotion = Emotion.NEUTRA) -> TTSResult:
        loop = asyncio.get_running_loop()
        started = loop.time()

        cached = self.cache.get(self.voice, text)
        if cached is not None:
            path, visemes = cached
            return TTSResult(
                audio_path=path,
                visemes=visemes,
                cache_hit=True,
                used_fallback=False,
                latency_ms=(loop.time() - started) * 1000.0,
                char_count=0,
            )

        try:
            wav_bytes, visemes = await asyncio.wait_for(
                self.client.synthesize(
                    text=text,
                    voice=self.voice,
                    emotion=emotion,
                    timeout_seconds=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds,
            )
            path = self.cache.put(self.voice, text, wav_bytes, visemes)
            return TTSResult(
                audio_path=path,
                visemes=visemes,
                cache_hit=False,
                used_fallback=False,
                latency_ms=(loop.time() - started) * 1000.0,
                char_count=len(text),
            )
        except Exception as exc:
            logger.warning("TTS falhou/expirou (%s); usando áudio de contingência", exc)
            path, visemes = self._ensure_fallback_audio()
            return TTSResult(
                audio_path=path,
                visemes=visemes,
                cache_hit=True,
                used_fallback=True,
                latency_ms=(loop.time() - started) * 1000.0,
                char_count=0,
            )
