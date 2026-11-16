"""Integração opcional com OBS Studio via obs-websocket (Seção 7.7)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class OBSController:
    """Controla troca para cena de contingência e consulta métricas do OBS."""

    def __init__(
        self,
        *,
        ws_url: str = "ws://127.0.0.1:4455",
        password: str = "",
        contingency_scene: str = "Contingencia",
        client: Any = None,
    ) -> None:
        self.ws_url = ws_url
        self.password = password
        self.contingency_scene = contingency_scene
        self._client = client
        self.connected = client is not None

    def connect(self) -> bool:
        if self._client is not None:
            self.connected = True
            return True
        try:
            import obsws_python as obs
        except ImportError:
            logger.info("obsws-python não instalado; controle OBS em modo passivo.")
            self.connected = False
            return False

        parsed = urlparse(self.ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4455
        try:
            self._client = obs.ReqClient(host=host, port=port, password=self.password, timeout=3)
            self.connected = True
            return True
        except Exception as exc:
            logger.warning("Não foi possível conectar ao OBS WebSocket: %s", exc)
            self.connected = False
            return False

    def switch_to_contingency(self) -> bool:
        if not self.connected or self._client is None:
            return False
        try:
            self._client.set_current_program_scene(self.contingency_scene)
            return True
        except Exception as exc:
            logger.warning("Erro ao trocar cena no OBS: %s", exc)
            return False

    def switch_scene(self, scene_name: str) -> bool:
        if not self.connected or self._client is None:
            return False
        try:
            self._client.set_current_program_scene(scene_name)
            return True
        except Exception as exc:
            logger.warning("Erro ao trocar para cena %s no OBS: %s", scene_name, exc)
            return False

    def get_dropped_frame_pct(self) -> float:
        if not self.connected or self._client is None:
            return 0.0
        try:
            stats = self._client.get_stats()
            total = float(getattr(stats, "output_total_frames", 0) or 0)
            skipped = float(getattr(stats, "output_skipped_frames", 0) or 0)
            if total <= 0:
                return 0.0
            return round((skipped / total) * 100.0, 2)
        except Exception:
            return 0.0
