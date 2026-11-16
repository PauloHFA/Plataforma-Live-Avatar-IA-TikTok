"""Supervisor com auto-restart em < 15 s (RNF-03) para orquestrador e Godot."""

from __future__ import annotations

import subprocess
import sys
import time


def run_supervised(
    cmd: list[str], *, restart_delay: float = 3.0, max_restarts: int | None = None
) -> int:
    restarts = 0
    while True:
        started = time.monotonic()
        proc = subprocess.Popen(cmd)
        code = proc.wait()
        if code == 0:
            return 0
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            return code
        uptime = time.monotonic() - started
        print(
            f"[supervisor] Processo encerrou (exit={code}, uptime={uptime:.1f}s). "
            f"Reiniciando em {restart_delay:.1f}s...",
            flush=True,
        )
        time.sleep(restart_delay)


if __name__ == "__main__":
    target = sys.argv[1:] or [sys.executable, "-m", "services.cli", "--source", "simulator"]
    raise SystemExit(run_supervised(target))
