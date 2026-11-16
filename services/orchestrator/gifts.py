"""Matriz de reação a presentes (tiers T1-T5, combos e metas) conforme Seção 9."""

from __future__ import annotations

from dataclasses import dataclass

from services.models import GiftMatrixConfig, GiftPayload, GiftTierConfig


@dataclass
class GiftReaction:
    tier: GiftTierConfig
    fx: str
    sound: str
    gesture: str
    coins_total: int
    combo_milestone: int | None
    goal_reached: bool
    cached_speech: str | None


class GiftReactionEngine:
    """Mapeia presentes em tiers, aplica customizações, degraus de combo e meta da live."""

    def __init__(self, config: GiftMatrixConfig) -> None:
        self.config = config
        self._sorted_tiers = sorted(config.tiers, key=lambda t: t.min_coins)
        self._goal_notified = False

    def resolve_tier(self, coins: int) -> GiftTierConfig:
        for tier in self._sorted_tiers:
            if tier.min_coins <= coins <= tier.max_coins:
                return tier
        return self._sorted_tiers[-1]

    def evaluate(
        self,
        *,
        user_nick: str,
        gift: GiftPayload,
        live_total_coins: int,
    ) -> GiftReaction:
        coins_total = max(1, gift.coins)
        tier = self.resolve_tier(coins_total)
        custom = self.config.custom_gifts.get(gift.name, {})

        fx = custom.get("fx", tier.fx)
        sound = custom.get("sound", tier.sound)
        gesture = custom.get("gesture", tier.gesture)

        combo_milestone: int | None = None
        for step in sorted(self.config.combo_steps, reverse=True):
            if gift.repeat >= step:
                combo_milestone = step
                break

        goal_reached = False
        if not self._goal_notified and live_total_coins >= self.config.live_goal.target_coins:
            self._goal_notified = True
            goal_reached = True

        cached_speech: str | None = None
        if not tier.use_llm and tier.cache_template:
            cached_speech = tier.cache_template.format(
                gift=gift.name,
                user=user_nick,
                repeat=gift.repeat,
            )

        return GiftReaction(
            tier=tier,
            fx=fx,
            sound=sound,
            gesture=gesture,
            coins_total=coins_total,
            combo_milestone=combo_milestone,
            goal_reached=goal_reached,
            cached_speech=cached_speech,
        )
