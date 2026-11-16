"""Adapter isolado para TikTokLive (biblioteca comunitária não oficial)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.ingest.base import LiveSource
from services.models import (
    EventPayload,
    EventType,
    EventUser,
    GiftPayload,
    NormalizedEvent,
)

logger = logging.getLogger(__name__)


class TikTokLiveSource(LiveSource):
    """Implementação LiveSource sobre TikTokLive, isolada atrás de adapter (R-01)."""

    def __init__(
        self,
        unique_id: str,
        *,
        client_factory: Any = None,
        max_backoff_seconds: float = 60.0,
        base_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            max_backoff_seconds=max_backoff_seconds,
            base_backoff_seconds=base_backoff_seconds,
        )
        self.unique_id = unique_id if unique_id.startswith("@") else f"@{unique_id}"
        self._client_factory = client_factory
        self._client: Any = None

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self.unique_id)
        try:
            from TikTokLive import TikTokLiveClient
        except ImportError as exc:
            raise RuntimeError(
                "TikTokLive não instalado. Instale o extra opcional `pip install '.[tiktok]'` "
                "ou utilize LIVE_SOURCE_MODE=simulator."
            ) from exc
        return TikTokLiveClient(unique_id=self.unique_id)

    @staticmethod
    def _extract_user(raw_user: Any) -> EventUser:
        uid = str(getattr(raw_user, "id", None) or getattr(raw_user, "unique_id", "anon"))
        nick = str(
            getattr(raw_user, "nickname", None)
            or getattr(raw_user, "unique_id", None)
            or "Visitante"
        )[:80]
        follow_info = getattr(raw_user, "follow_info", None)
        is_follower = bool(getattr(follow_info, "follow_status", 0))
        return EventUser(id=uid, nick=nick, is_follower=is_follower)

    @classmethod
    def normalize_raw_event(cls, kind: str, raw_event: Any) -> NormalizedEvent:
        """Converte objeto de evento TikTokLive para NormalizedEvent."""
        raw_user = getattr(raw_event, "user", None)
        user = (
            cls._extract_user(raw_user)
            if raw_user is not None
            else EventUser(id="system", nick="TikTok")
        )

        if kind == "chat":
            text = str(getattr(raw_event, "comment", "") or "")
            return NormalizedEvent(
                type=EventType.CHAT,
                user=user,
                payload=EventPayload(text=text),
            )
        if kind == "gift":
            gift = getattr(raw_event, "gift", None)
            name = str(getattr(gift, "name", "Gift"))
            diamond = int(getattr(gift, "diamond_count", 1) or 1)
            repeat = int(getattr(raw_event, "repeat_count", 1) or 1)
            streakable = bool(getattr(gift, "streakable", False))
            streaking = bool(getattr(raw_event, "streaking", False))
            streak_final = not (streakable and streaking)
            return NormalizedEvent(
                type=EventType.GIFT,
                user=user,
                payload=EventPayload(
                    gift=GiftPayload(
                        name=name,
                        coins=max(1, diamond * repeat),
                        repeat=max(1, repeat),
                        streak_final=streak_final,
                        gift_id=str(getattr(gift, "id", "")) or None,
                    )
                ),
            )
        if kind == "follow":
            return NormalizedEvent(type=EventType.FOLLOW, user=user)
        if kind == "share":
            return NormalizedEvent(type=EventType.SHARE, user=user)
        if kind == "like":
            likes = int(getattr(raw_event, "count", 1) or 1)
            return NormalizedEvent(
                type=EventType.LIKE,
                user=user,
                payload=EventPayload(likes=likes),
            )
        if kind == "join":
            return NormalizedEvent(type=EventType.JOIN, user=user)
        if kind == "viewer_count":
            count = int(getattr(raw_event, "m_total", 0) or getattr(raw_event, "viewer_count", 0))
            return NormalizedEvent(
                type=EventType.VIEWER_COUNT,
                user=user,
                payload=EventPayload(viewer_count=max(0, count)),
            )
        return NormalizedEvent(type=EventType.SYSTEM, user=user)

    async def connect(self) -> None:
        self._client = self._build_client()
        try:
            from TikTokLive.events import (
                CommentEvent,
                ConnectEvent,
                FollowEvent,
                GiftEvent,
                JoinEvent,
                LikeEvent,
                ShareEvent,
            )

            async def _on_connect(_ev: Any) -> None:
                self._connected = True
                logger.info("Conectado ao TikTok LIVE: %s", self.unique_id)

            async def _on_comment(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("chat", ev))

            async def _on_gift(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("gift", ev))

            async def _on_follow(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("follow", ev))

            async def _on_share(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("share", ev))

            async def _on_like(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("like", ev))

            async def _on_join(ev: Any) -> None:
                await self.emit_event(self.normalize_raw_event("join", ev))

            handlers = (
                (ConnectEvent, _on_connect),
                (CommentEvent, _on_comment),
                (GiftEvent, _on_gift),
                (FollowEvent, _on_follow),
                (ShareEvent, _on_share),
                (LikeEvent, _on_like),
                (JoinEvent, _on_join),
            )
            for event_cls, handler in handlers:
                self._client.on(event_cls)(handler)

        except ImportError:
            # Permite uso em testes com client_factory mockada sem TikTokLive instalada
            pass

        self._connected = True
        start_fn = getattr(self._client, "start", None)
        if start_fn is not None:
            res = start_fn()
            if asyncio.iscoroutine(res):
                await res

    async def close(self) -> None:
        self._closed = True
        self._connected = False
        await self.flush_pending_streaks()
        if self._client is not None:
            disconnect = getattr(self._client, "disconnect", None) or getattr(
                self._client, "stop", None
            )
            if disconnect is not None:
                res = disconnect()
                if asyncio.iscoroutine(res):
                    await res
