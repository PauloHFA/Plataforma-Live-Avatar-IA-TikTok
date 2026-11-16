"""Entrypoint CLI do orquestrador + painel do operador + fonte simulada ou TikTokLive."""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from services.ingest.base import LiveSource
from services.ingest.simulator_source import SimulatorSource
from services.ingest.tiktoklive_source import TikTokLiveSource
from services.orchestrator.api import create_operator_app
from services.orchestrator.config import Settings
from services.orchestrator.pipeline import OrchestratorService


def build_runtime(
    settings: Settings,
    *,
    source_mode: str | None = None,
    sim_rate: float = 1.0,
) -> tuple[OrchestratorService, FastAPI]:
    mode = (source_mode or settings.live_source_mode).lower()
    source: LiveSource
    if mode == "tiktok":
        source = TikTokLiveSource(settings.tiktok_unique_id)
    else:
        source = SimulatorSource(events_per_second=sim_rate)

    orchestrator = OrchestratorService(settings, source=source)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await orchestrator.start(start_ws=True, start_source=True)
        try:
            yield
        finally:
            await orchestrator.stop()

    app = create_operator_app(orchestrator)
    app.router.lifespan_context = lifespan
    return orchestrator, app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Live Automática com Avatar de IA no TikTok")
    parser.add_argument("--source", choices=["simulator", "tiktok"], default=None)
    parser.add_argument("--sim-rate", type=float, default=1.0)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    settings = Settings()
    _, app = build_runtime(settings, source_mode=args.source, sim_rate=args.sim_rate)
    uvicorn.run(
        app,
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
