"""Geração de timeline de visemes por texto e análise de amplitude WAV."""

from __future__ import annotations

import io
import math
import struct
import wave

from services.models import VisemeFrame

CHAR_TO_VISEME = {
    "a": "aa",
    "á": "aa",
    "à": "aa",
    "ã": "aa",
    "â": "aa",
    "e": "E",
    "é": "E",
    "ê": "E",
    "i": "ih",
    "í": "ih",
    "o": "oh",
    "ó": "oh",
    "ô": "oh",
    "õ": "oh",
    "u": "ou",
    "ú": "ou",
    "p": "PP",
    "b": "PP",
    "m": "PP",
    "f": "FF",
    "v": "FF",
    "t": "DD",
    "d": "DD",
    "s": "SS",
    "z": "SS",
    "ç": "SS",
    "r": "RR",
    "l": "nn",
    "n": "nn",
}


def generate_visemes_from_text(text: str, duration_seconds: float) -> list[VisemeFrame]:
    """Cria linha do tempo de visemes sincronizada com a duração do áudio."""
    cleaned = [c.lower() for c in text if c.isalpha() or c == " "]
    if not cleaned or duration_seconds <= 0:
        return [VisemeFrame(t=0.0, v="sil")]

    step = max(0.04, duration_seconds / max(1, len(cleaned)))
    frames: list[VisemeFrame] = [VisemeFrame(t=0.0, v="sil")]
    t = 0.05
    last_viseme = "sil"
    for ch in cleaned:
        vis = "sil" if ch == " " else CHAR_TO_VISEME.get(ch, "kk")
        if vis != last_viseme:
            frames.append(VisemeFrame(t=round(min(t, duration_seconds), 3), v=vis))
            last_viseme = vis
        t += step
    frames.append(VisemeFrame(t=round(duration_seconds, 3), v="sil"))
    return frames


def synthesize_sine_wav(text: str, *, sample_rate: int = 16000) -> tuple[bytes, float]:
    """Gera WAV PCM válido com modulação silábica proporcional ao texto."""
    words = max(1, len(text.split()))
    duration = max(0.6, min(8.0, words * 0.34))
    total_samples = int(sample_rate * duration)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_samples):
            t = i / sample_rate
            # Envelope silábico (~4 Hz) + portadora suave (220 Hz)
            env = 0.5 * (1.0 + math.sin(2.0 * math.pi * 4.2 * t - math.pi / 2.0))
            sample = int(8500 * env * math.sin(2.0 * math.pi * 220.0 * t))
            frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
        wf.writeframes(bytes(frames))

    return buf.getvalue(), duration
