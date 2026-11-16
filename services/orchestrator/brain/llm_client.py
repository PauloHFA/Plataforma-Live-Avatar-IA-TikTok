"""Cliente LLM desacoplado, controle de timeout (<=6s), orçamento e fallback seguro."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from services.models import Emotion, LLMBrainOutput, PersonaProfile
from services.orchestrator.brain.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class BrainGenerationResult:
    output: LLMBrainOutput
    model_used: str
    used_fallback: bool
    estimated_cost_usd: float
    latency_ms: float


class LLMClient(ABC):
    """Interface intercambiável para provedores LLM."""

    @abstractmethod
    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_seconds: float,
    ) -> tuple[LLMBrainOutput, int, int]:
        """Retorna (LLMBrainOutput, tokens_in, tokens_out)."""


class OpenAICompatibleLLMClient(LLMClient):
    """Cliente HTTP assíncrono compatível com OpenAI, Ollama e vLLM."""

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient()

    @staticmethod
    def parse_brain_json(raw_text: str) -> LLMBrainOutput:
        cleaned = raw_text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        elif not cleaned.startswith("{"):
            brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if brace:
                cleaned = brace.group(0)
        data = json.loads(cleaned)
        return LLMBrainOutput.model_validate(data)

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_seconds: float,
    ) -> tuple[LLMBrainOutput, int, int]:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY não configurada")

        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "temperature": 0.8,
                "max_tokens": 120,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        tok_in = int(usage.get("prompt_tokens", 120))
        tok_out = int(usage.get("completion_tokens", 40))
        return self.parse_brain_json(content), tok_in, tok_out

    async def close(self) -> None:
        await self._client.aclose()


class RuleMockLLMClient(LLMClient):
    """Cérebro determinístico local para testes, simulador e operação offline."""

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_seconds: float,
    ) -> tuple[LLMBrainOutput, int, int]:
        del system_prompt, model, timeout_seconds
        user_match = re.search(
            r'<chat_message user="([^"]+)">(.*?)</chat_message>', user_prompt, re.S
        )
        nick = user_match.group(1) if user_match else "tripulante"
        msg = user_match.group(2).strip() if user_match else ""

        if "presente" in user_prompt.lower() or "Tipo de evento: gift" in user_prompt:
            fala = f"Uau {nick}, muito obrigada pelo presente incrível! A nave até brilhou aqui!"
            return LLMBrainOutput(fala=fala, emocao=Emotion.SURPRESA, gesto="heart"), 80, 24

        if "?" in msg:
            fala = f"Boa pergunta, {nick}! Tô adorando explorar o espaço com vocês hoje no chat!"
            return LLMBrainOutput(fala=fala, emocao=Emotion.ALEGRE, gesto="wave"), 80, 22

        fala = f"Salve, {nick}! Que massa ter você aqui na live espacial com a gente!"
        return LLMBrainOutput(fala=fala, emocao=Emotion.ALEGRE, gesto="wave"), 70, 20


class BrainEngine:
    """Seleciona modelo (rápido vs capaz), aplica timeout e aciona contingência (RF-16)."""

    def __init__(
        self,
        *,
        persona: PersonaProfile,
        client: LLMClient,
        model_fast: str = "gpt-4o-mini",
        model_strong: str = "gpt-4o",
        timeout_seconds: float = 6.0,
        seed: int = 42,
    ) -> None:
        self.persona = persona
        self.client = client
        self.model_fast = model_fast
        self.model_strong = model_strong
        self.timeout_seconds = timeout_seconds
        self._rng = random.Random(seed)
        self._system_prompt = build_system_prompt(persona)

    def update_persona(self, persona: PersonaProfile) -> None:
        self.persona = persona
        self._system_prompt = build_system_prompt(persona)

    def pick_fallback(self) -> LLMBrainOutput:
        line = self._rng.choice(self.persona.fallback_lines)
        return LLMBrainOutput(fala=line, emocao=Emotion.NEUTRA, gesto="wave")

    async def respond(
        self,
        *,
        event_kind: str,
        user_nick: str,
        content: str,
        session_summary: str,
        recent_exchanges: list[dict[str, str]],
        use_strong_model: bool = False,
        economy_mode: bool = False,
    ) -> BrainGenerationResult:
        if economy_mode:
            return BrainGenerationResult(
                output=self.pick_fallback(),
                model_used="economy_fallback",
                used_fallback=True,
                estimated_cost_usd=0.0,
                latency_ms=1.0,
            )

        model = self.model_strong if use_strong_model else self.model_fast
        user_prompt = build_user_prompt(
            event_kind=event_kind,
            user_nick=user_nick,
            content=content,
            session_summary=session_summary,
            recent_exchanges=recent_exchanges,
        )

        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            output, tok_in, tok_out = await asyncio.wait_for(
                self.client.generate_json(
                    system_prompt=self._system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    timeout_seconds=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds,
            )
            elapsed_ms = (loop.time() - started) * 1000.0
            # Estimativa simples de custo por token
            rate = 0.000005 if use_strong_model else 0.0000006
            cost = (tok_in + tok_out * 3) * rate
            return BrainGenerationResult(
                output=output,
                model_used=model,
                used_fallback=False,
                estimated_cost_usd=cost,
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (loop.time() - started) * 1000.0
            logger.warning("LLM falhou/expirou (%s); usando fala de contingência", exc)
            return BrainGenerationResult(
                output=self.pick_fallback(),
                model_used="fallback",
                used_fallback=True,
                estimated_cost_usd=0.0,
                latency_ms=elapsed_ms,
            )
