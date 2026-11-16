"""Persistência SQLite assíncrona (Seção 8.4) com suporte a retenção e exclusão LGPD (RNF-06)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from services.models import NormalizedEvent


class AuditRepository:
    """Gerencia tabelas events, utterances, users e sessions em SQLite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                user_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                moderation_result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

            CREATE TABLE IF NOT EXISTS utterances (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                event_id TEXT,
                text TEXT NOT NULL,
                emotion TEXT NOT NULL,
                llm_ms REAL NOT NULL,
                tts_ms REAL NOT NULL,
                total_ms REAL NOT NULL,
                cost_estimate REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_utterances_ts ON utterances(ts);

            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nick TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                total_coins INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                peak_viewers INTEGER NOT NULL DEFAULT 0,
                total_coins INTEGER NOT NULL DEFAULT 0,
                total_events INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self._conn.commit()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("AuditRepository não inicializado; chame initialize() antes.")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def start_session(self, session_id: str) -> None:
        conn = self._require_conn()
        now_iso = datetime.now(UTC).isoformat()
        await conn.execute(
            "INSERT OR REPLACE INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, now_iso),
        )
        await conn.commit()

    async def end_session(
        self,
        session_id: str,
        *,
        peak_viewers: int,
        total_coins: int,
        total_events: int,
    ) -> None:
        conn = self._require_conn()
        now_iso = datetime.now(UTC).isoformat()
        await conn.execute(
            """
            UPDATE sessions
               SET ended_at = ?, peak_viewers = ?, total_coins = ?, total_events = ?
             WHERE id = ?
            """,
            (now_iso, peak_viewers, total_coins, total_events, session_id),
        )
        await conn.commit()

    async def upsert_user(
        self,
        *,
        user_id: str,
        nick: str,
        coins_delta: int = 0,
        banned: bool | None = None,
    ) -> None:
        conn = self._require_conn()
        now_iso = datetime.now(UTC).isoformat()
        await conn.execute(
            """
            INSERT INTO users (user_id, nick, first_seen, last_seen, total_coins, banned)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nick = excluded.nick,
                last_seen = excluded.last_seen,
                total_coins = users.total_coins + excluded.total_coins,
                banned = CASE WHEN ? IS NOT NULL THEN ? ELSE users.banned END
            """,
            (
                user_id,
                nick,
                now_iso,
                now_iso,
                max(0, coins_delta),
                1 if banned else 0,
                None if banned is None else (1 if banned else 0),
                None if banned is None else (1 if banned else 0),
            ),
        )
        await conn.commit()

    async def log_event(self, event: NormalizedEvent, moderation_result: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT OR REPLACE INTO events (id, ts, type, user_id, payload_json, moderation_result)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.ts.isoformat(),
                event.type.value,
                event.user.id,
                json.dumps(event.payload.model_dump(), ensure_ascii=False),
                moderation_result,
            ),
        )
        await conn.commit()

    async def log_utterance(
        self,
        *,
        utterance_id: str,
        event_id: str | None,
        text: str,
        emotion: str,
        llm_ms: float,
        tts_ms: float,
        total_ms: float,
        cost_estimate: float,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT OR REPLACE INTO utterances
            (id, ts, event_id, text, emotion, llm_ms, tts_ms, total_ms, cost_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utterance_id,
                datetime.now(UTC).isoformat(),
                event_id,
                text,
                emotion,
                llm_ms,
                tts_ms,
                total_ms,
                cost_estimate,
            ),
        )
        await conn.commit()

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, ts, type, user_id, payload_json, moderation_result "
            "FROM events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def purge_expired(self, retention_days: int = 30) -> int:
        """Remove eventos e falas anteriores ao período de retenção LGPD."""
        conn = self._require_conn()
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        cur1 = await conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        cur2 = await conn.execute("DELETE FROM utterances WHERE ts < ?", (cutoff,))
        await conn.commit()
        return (cur1.rowcount or 0) + (cur2.rowcount or 0)

    async def delete_user_data(self, user_id: str) -> int:
        """Exclusão sob pedido do titular (LGPD - RNF-06)."""
        conn = self._require_conn()
        cur1 = await conn.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
        cur2 = await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await conn.commit()
        return (cur1.rowcount or 0) + (cur2.rowcount or 0)
