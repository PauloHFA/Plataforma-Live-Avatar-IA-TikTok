"""Testes unitários de CLI, OBSController, MetricsRegistry, OpenAIClient e SimulatorSource."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from services.cli import build_runtime
from services.ingest.simulator_source import SimulatorSource
from services.orchestrator.brain.llm_client import OpenAICompatibleLLMClient
from services.orchestrator.config import Settings
from services.orchestrator.metrics import MetricsRegistry
from services.orchestrator.obs_control import OBSController


@pytest.mark.unit
def test_obs_controller_with_mock_client():
    fake_client = SimpleNamespace(
        scenes=[],
        set_current_program_scene=lambda name: fake_client.scenes.append(name),
        get_stats=lambda: SimpleNamespace(output_total_frames=1000, output_skipped_frames=25),
    )
    obs = OBSController(client=fake_client, contingency_scene="SafeScene")
    assert obs.connect() is True
    assert obs.switch_to_contingency() is True
    assert obs.switch_scene("studio_space") is True
    assert fake_client.scenes == ["SafeScene", "studio_space"]
    assert obs.get_dropped_frame_pct() == pytest.approx(2.5)


@pytest.mark.unit
def test_metrics_thresholds_and_prometheus():
    m = MetricsRegistry()
    for _ in range(12):
        m.record_llm(failed=True)
        m.record_tts(failed=True)
    m.record_latency(12_000.0)
    m.godot_fps = 18.0
    m.obs_dropped_frame_pct = 4.0

    fired = m.check_thresholds(queue_size=60, spent_usd=4.5, budget_usd=5.0)
    assert len(fired) == 6
    prom = m.to_prometheus(queue_size=60, spent_usd=4.5)
    assert "live_latency_p95_ms 12000.0" in prom


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_compatible_llm_client_and_simulator_synthetic():
    client = OpenAICompatibleLLMClient(base_url="https://mock.llm/v1", api_key="key-test")
    with respx.mock(base_url="https://mock.llm/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '```json\n{"fala": "Oi!", '
                                    '"emocao": "alegre", "gesto": "wave"}\n```'
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 15},
                },
            )
        )
        out, tok_in, tok_out = await client.generate_json(
            system_prompt="sys",
            user_prompt="usr",
            model="gpt-4o-mini",
            timeout_seconds=3.0,
        )
        assert out.fala == "Oi!"
        assert tok_in == 50 and tok_out == 15
    await client.close()

    # SimulatorSource gerando eventos sintéticos variados
    sim = SimulatorSource(events_per_second=100.0, max_events=16, seed=7)
    collected = []
    sim.on_event(lambda e: collected.append(e))
    await sim.connect()
    await sim.close()
    assert len(collected) >= 16


@pytest.mark.unit
def test_cli_build_runtime():
    orch, app = build_runtime(Settings(), source_mode="simulator", sim_rate=2.0)
    assert orch.source is not None
    assert app.title.startswith("Live IA TikTok")
