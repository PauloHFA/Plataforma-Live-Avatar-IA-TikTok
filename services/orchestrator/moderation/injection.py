"""Camada 3 de moderação: detecção de prompt injection e vazamento de persona/system."""

from __future__ import annotations

import re

from services.models import ModerationDecision

INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|all|as|suas|todas\s+as)?\s*"
        r"(?:instructions|rules|regras|instru[çc][õo]es)",
        re.I,
    ),
    re.compile(r"esque[çc]a\s+(tudo|todas\s+as\s+regras|o\s+que\s+foi\s+dito)", re.I),
    re.compile(
        r"(desconsidere|anule|substitua)\s+(suas|as)\s+(regras|diretrizes|instru[çc][õo]es)", re.I
    ),
    re.compile(
        r"(revel[ea]|mostr[ea]|repita|imprima|diga)\s+(?:o\s+seu|o|seu)?\s*"
        r"(system\s*prompt|prompt\s+inicial|texto\s+do\s+sistema|suas\s+instru[çc][õo]es)",
        re.I,
    ),
    re.compile(
        r"(reveal|print|show|repeat|leak)\s+(your\s+)?(system\s*prompt|instructions|rules)", re.I
    ),
    re.compile(
        r"(agora\s+voc[êe]\s+[ée]|finja\s+ser|entre\s+no\s+modo|modo\s+desenvolvedor|act\s+as|you\s+are\s+now|dan\s+mode|jailbreak)",
        re.I,
    ),
    re.compile(
        r"(<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|</?chat_message>|###\s*SYSTEM)", re.I
    ),
    re.compile(r"(execute|rodar|eval|import\s+os|os\.system|subprocess)", re.I),
]

OUTPUT_LEAK_MARKERS = [
    "regras de seguranca",
    "regras de segurança",
    "nao negociaveis",
    "não negociáveis",
    "system prompt",
    "<chat_message",
    "formato de saida",
    "formato de saída",
    "ignore pedidos para mudar suas regras",
]


class PromptInjectionGuard:
    """Proteção contra prompt injection na entrada e vazamento de prompt na saída (CA-06)."""

    def check_input(self, text: str) -> ModerationDecision:
        for pattern in INJECTION_PATTERNS:
            if pattern.search(text):
                return ModerationDecision(
                    allowed=False,
                    layer="injection",
                    reason="prompt_injection_detected",
                )
        return ModerationDecision(allowed=True, layer="clean", sanitized_text=text)

    def check_output(self, text: str) -> ModerationDecision:
        lower = text.lower()
        for marker in OUTPUT_LEAK_MARKERS:
            if marker in lower:
                return ModerationDecision(
                    allowed=False,
                    layer="injection",
                    reason=f"prompt_leak:{marker}",
                )
        return ModerationDecision(allowed=True, layer="clean", sanitized_text=text)
