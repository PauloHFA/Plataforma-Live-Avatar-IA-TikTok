"""Cache local determinístico de áudio e visemes para saudações e falas frequentes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.models import VisemeFrame


class AudioCache:
    """Armazena WAV + JSON de visemes por hash SHA-256 de (voz, texto)."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(voice: str, text: str) -> str:
        digest = hashlib.sha256(f"{voice}::{text.strip()}".encode()).hexdigest()
        return digest[:24]

    def get(self, voice: str, text: str) -> tuple[Path, list[VisemeFrame]] | None:
        key = self._key(voice, text)
        wav_path = self.cache_dir / f"{key}.wav"
        meta_path = self.cache_dir / f"{key}.visemes.json"
        if not wav_path.exists() or not meta_path.exists():
            return None
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        frames = [VisemeFrame.model_validate(item) for item in raw]
        return wav_path.resolve(), frames

    def put(
        self,
        voice: str,
        text: str,
        wav_bytes: bytes,
        visemes: list[VisemeFrame],
    ) -> Path:
        key = self._key(voice, text)
        wav_path = self.cache_dir / f"{key}.wav"
        meta_path = self.cache_dir / f"{key}.visemes.json"
        wav_path.write_bytes(wav_bytes)
        meta_path.write_text(
            json.dumps([v.model_dump() for v in visemes], ensure_ascii=False),
            encoding="utf-8",
        )
        return wav_path.resolve()
