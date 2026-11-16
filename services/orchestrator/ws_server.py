"""Servidor WebSocket local (Seção 8.2/8.3) para comunicação com Godot 4.x."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel
from websockets.asyncio.server import Server, ServerConnection, serve

from services.models import GodotInboundMessage

logger = logging.getLogger(__name__)

InboundCallback = Callable[[GodotInboundMessage], Awaitable[None] | None]


class AvatarWebSocketServer:
    """Publica comandos JSON para clientes Godot conectados em ws://host:port/avatar."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._server: Server | None = None
        self._clients: set[ServerConnection] = set()
        self._callbacks: list[InboundCallback] = []
        self._lock = asyncio.Lock()

    @property
    def bound_port(self) -> int:
        if self._server is not None and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self.port

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def on_message(self, cb: InboundCallback) -> None:
        self._callbacks.append(cb)

    async def start(self) -> None:
        self._server = await serve(self._handle_client, self.host, self.port)
        logger.info("WebSocket Godot ativo em ws://%s:%s/avatar", self.host, self.bound_port)

    async def stop(self) -> None:
        async with self._lock:
            for ws in list(self._clients):
                await ws.close()
            self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, ws: ServerConnection) -> None:
        async with self._lock:
            self._clients.add(ws)
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    msg = GodotInboundMessage.model_validate(data)
                except Exception as exc:
                    logger.warning("Mensagem inválida da Godot: %s", exc)
                    continue
                for cb in list(self._callbacks):
                    res = cb(msg)
                    if inspect.isawaitable(res):
                        await res
        except Exception as exc:
            logger.debug("Conexão Godot encerrada: %s", exc)
        finally:
            async with self._lock:
                self._clients.discard(ws)

    async def broadcast(self, command: BaseModel | dict[str, Any]) -> int:
        payload = (
            command.model_dump_json()
            if isinstance(command, BaseModel)
            else json.dumps(command, ensure_ascii=False)
        )
        sent = 0
        async with self._lock:
            stale: list[ServerConnection] = []
            for ws in list(self._clients):
                try:
                    await ws.send(payload)
                    sent += 1
                except Exception:
                    stale.append(ws)
            for ws in stale:
                self._clients.discard(ws)
        return sent
