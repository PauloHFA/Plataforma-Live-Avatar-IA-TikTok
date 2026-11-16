"""Camada 1 de moderação: regras determinísticas baratas (links, PII, spam, CAPS, lista dura)."""

from __future__ import annotations

import re

from services.models import ModerationDecision

URL_RE = re.compile(
    r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|net|org|br|io|xyz|me|gg|ly)(?:/\S*)?)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}[-\s.]?\d{4}\b")
CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
REPEATED_CHAR_RE = re.compile(r"(.)\1{7,}", re.IGNORECASE)


class RuleModerator:
    """Filtros rápidos de entrada e saída (RF-05, RF-07, Seção 7.3)."""

    def __init__(
        self,
        *,
        banned_terms: list[str] | None = None,
        banned_users: set[str] | None = None,
        max_input_chars: int = 300,
        max_output_words: int = 35,
    ) -> None:
        self.banned_terms: set[str] = {t.strip().lower() for t in (banned_terms or []) if t.strip()}
        self.banned_users: set[str] = set(banned_users or set())
        self.max_input_chars = max_input_chars
        self.max_output_words = max_output_words

    def add_banned_term(self, term: str) -> None:
        cleaned = term.strip().lower()
        if cleaned:
            self.banned_terms.add(cleaned)

    def remove_banned_term(self, term: str) -> None:
        self.banned_terms.discard(term.strip().lower())

    def ban_user(self, user_id: str) -> None:
        self.banned_users.add(user_id)

    def unban_user(self, user_id: str) -> None:
        self.banned_users.discard(user_id)

    def check_input(self, user_id: str, text: str) -> ModerationDecision:
        if user_id in self.banned_users:
            return ModerationDecision(allowed=False, layer="rules", reason="banned_user")

        stripped = text.strip()
        if not stripped:
            return ModerationDecision(allowed=False, layer="rules", reason="empty_text")
        if len(stripped) > self.max_input_chars:
            return ModerationDecision(allowed=False, layer="rules", reason="too_long")

        lower = stripped.lower()
        for term in self.banned_terms:
            if term in lower:
                return ModerationDecision(
                    allowed=False, layer="rules", reason=f"banned_term:{term}"
                )

        if URL_RE.search(stripped):
            return ModerationDecision(allowed=False, layer="rules", reason="url_not_allowed")
        if EMAIL_RE.search(stripped) or PHONE_RE.search(stripped) or CPF_RE.search(stripped):
            return ModerationDecision(allowed=False, layer="rules", reason="pii_detected")
        if REPEATED_CHAR_RE.search(stripped):
            return ModerationDecision(allowed=False, layer="rules", reason="spam_repetition")

        alpha_chars = [c for c in stripped if c.isalpha()]
        if len(alpha_chars) >= 12:
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            if upper_ratio > 0.75:
                return ModerationDecision(allowed=False, layer="rules", reason="excessive_caps")

        # Spam de palavras repetidas (>= 4 repetições da mesma palavra)
        words = re.findall(r"\w+", lower)
        if len(words) >= 5:
            counts: dict[str, int] = {}
            for w in words:
                counts[w] = counts.get(w, 0) + 1
                if counts[w] >= 4:
                    return ModerationDecision(
                        allowed=False, layer="rules", reason="spam_word_repetition"
                    )

        return ModerationDecision(allowed=True, layer="clean", sanitized_text=stripped)

    def check_output(self, text: str) -> ModerationDecision:
        cleaned = URL_RE.sub("", text).strip()
        cleaned = EMAIL_RE.sub("", cleaned).strip()
        if not cleaned:
            return ModerationDecision(allowed=False, layer="rules", reason="empty_output")

        lower = cleaned.lower()
        for term in self.banned_terms:
            if term in lower:
                return ModerationDecision(
                    allowed=False, layer="rules", reason=f"output_banned_term:{term}"
                )

        words = cleaned.split()
        if len(words) > self.max_output_words:
            cleaned = " ".join(words[: self.max_output_words]).rstrip(",;:") + "..."

        return ModerationDecision(allowed=True, layer="clean", sanitized_text=cleaned)
