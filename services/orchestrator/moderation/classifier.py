"""Camada 2 (classificador) e pipeline completo de moderação entrada/saída."""

from __future__ import annotations

import logging
import re

import httpx

from services.models import ModerationDecision
from services.orchestrator.moderation.injection import PromptInjectionGuard
from services.orchestrator.moderation.rules import RuleModerator

logger = logging.getLogger(__name__)

# Termos de risco para classificação semântica rápida (ódio, sexual, violência, golpes)
RISK_CATEGORIES: dict[str, re.Pattern[str]] = {
    "hate_harassment": re.compile(
        r"\b(odeio\s+todos|morte\s+aos|lixo\s+humano|se\s+mata|vai\s+morrer|racist\w*|nazist\w*)\b",
        re.I,
    ),
    "self_harm": re.compile(
        r"\b(suic[ií]d\w*|me\s+matar|se\s+cortar|autoles[aã]o)\b",
        re.I,
    ),
    "violence": re.compile(
        r"\b(matar\s+algu[ée]m|fazer\s+massacre|atirar\s+em|explodir\s+a)\b",
        re.I,
    ),
    "sexual": re.compile(
        r"\b(porn[oô]\w*|nudes\b|sexo\s+expl[ií]cito|putaria|onlyfans\s+gratis)\b",
        re.I,
    ),
    "scam_coercion": re.compile(
        r"\b(mande\s+presente\s+ou\s+eu|pix\s+dobrado|ganhe\s+dinheiro\s+f[áa]cil|sorteio\s+de\s+pix)\b",
        re.I,
    ),
    "sensitive_politics_religion": re.compile(
        r"\b(vote\s+no\s+candidato|partido\s+pol[ií]tico\s+tal|religi[aã]o\s+falsa)\b",
        re.I,
    ),
}


class ContentClassifier:
    """Classificador local determinístico com suporte opcional a endpoint remoto."""

    def __init__(
        self,
        *,
        api_url: str = "",
        api_key: str = "",
        timeout_seconds: float = 2.0,
    ) -> None:
        self.api_url = api_url.strip()
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def classify(self, text: str) -> ModerationDecision:
        for category, pattern in RISK_CATEGORIES.items():
            if pattern.search(text):
                return ModerationDecision(
                    allowed=False,
                    layer="classifier",
                    reason=f"category:{category}",
                )

        if self.api_url and self.api_key:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(
                        self.api_url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"input": text},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("results") or []
                        if results and results[0].get("flagged"):
                            return ModerationDecision(
                                allowed=False,
                                layer="classifier",
                                reason="external_moderation_flagged",
                            )
            except Exception as exc:
                logger.warning("Falha no classificador remoto (fallback local): %s", exc)

        return ModerationDecision(allowed=True, layer="clean", sanitized_text=text)


class ModerationPipeline:
    """Orquestra as camadas 1 (Regras), 2 (Classificador) e 3 (Anti-injeção)."""

    def __init__(
        self,
        *,
        banned_terms: list[str] | None = None,
        api_url: str = "",
        api_key: str = "",
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.rules = RuleModerator(banned_terms=banned_terms)
        self.injection = PromptInjectionGuard()
        self.classifier = ContentClassifier(api_url=api_url, api_key=api_key)

    async def check_input(self, user_id: str, text: str) -> ModerationDecision:
        if not self.enabled:
            return ModerationDecision(allowed=True, layer="clean", sanitized_text=text)

        d1 = self.rules.check_input(user_id, text)
        if not d1.allowed:
            return d1

        sanitized = d1.sanitized_text or text
        d3 = self.injection.check_input(sanitized)
        if not d3.allowed:
            return d3

        d2 = await self.classifier.classify(sanitized)
        if not d2.allowed:
            return d2

        return ModerationDecision(allowed=True, layer="clean", sanitized_text=sanitized)

    async def check_output(self, text: str) -> ModerationDecision:
        if not self.enabled:
            return ModerationDecision(allowed=True, layer="clean", sanitized_text=text)

        d1 = self.rules.check_output(text)
        if not d1.allowed:
            return d1

        sanitized = d1.sanitized_text or text
        d3 = self.injection.check_output(sanitized)
        if not d3.allowed:
            return d3

        d2 = await self.classifier.classify(sanitized)
        if not d2.allowed:
            return d2

        return ModerationDecision(allowed=True, layer="clean", sanitized_text=sanitized)
