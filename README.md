# Plataforma-Live-Avatar-IA-TikTok

Implementação modular de live automática vertical (`1080x1920`) no TikTok conduzida por um avatar VTuber controlado por IA *(pacote/CLI interno: `live-ia-tiktok`)*, baseada em `Documentacao_Live_IA_TikTok.pdf`.

## Arquitetura

- **`services/ingest/`**: interface única `LiveSource` com `SimulatorSource` (incluindo combos/streaks) e adapter isolado `TikTokLiveSource`, reconexão com backoff exponencial (`1s..60s`) e alerta operacional após 3 falhas.
- **`services/orchestrator/`**:
  - `queue.py`: fila com prioridades `P0..P5`, TTL de 20s em menções (`P2`), agregação em lote de follows/shares (`P3`), rate limit por usuário (`35s`, isento para doadores) e descarte seguro sob pico.
  - `moderation/`: moderação em 3 camadas na entrada e saída (`rules.py`, `injection.py`, `classifier.py`).
  - `brain/`: prompt builder isolado (`<chat_message>`), seleção de modelo rápido/capaz, timeout de `6s` e contingência offline (`RuleMockLLMClient` / `OpenAICompatibleLLMClient`).
  - `voice/`: TTS desacoplado (`MockSineTTSClient` e `AzureSpeechTTSClient`), cache SHA-256 em disco e linha do tempo de visemes.
  - `gifts.py` & `minigames.py`: matriz T1–T5, combos, meta da live e comandos `!danca`, `!cor`, `!votar`.
  - `storage.py`: persistência SQLite assíncrona (`events`, `utterances`, `users`, `sessions`) com expurgo por retenção e exclusão LGPD (`DELETE /api/users/{user_id}`).
  - `ws_server.py` & `obs_control.py`: servidor WebSocket `ws://127.0.0.1:8765/avatar` para Godot 4.x e controle de cena/métricas OBS.
  - `api.py` & `services/operator-panel/index.html`: painel web e API REST com Kill Switch, pausa/retomada, injeção de fala, lista proibida, banimento, veto e `/metrics`.
- **`godot/`**: projeto Godot 4.x `1080x1920`, bus de áudio `Voice` com `AudioEffectSpectrumAnalyzer`, `NetClient`, `AvatarController`, `LipSyncController`, `EmotionSystem`, `GiftFX`, `OverlayUI` (com selo `Avatar de IA`) e `SafeModeOverlay`.
- **`scripts/supervisor.py`**: supervisor com auto-restart para execução contínua.

## Como executar localmente (Modo Simulador)

```bash
# 1. Subir orquestrador + simulador + painel operador (http://127.0.0.1:8080)
py -3.12 -m services.cli --source simulator --sim-rate 1.0

# 2. (Opcional) Abrir cena Godot conectando em ws://127.0.0.1:8765/avatar
godot --path godot
```

## Testes e Qualidade

```bash
py -3.12 -m ruff check .
py -3.12 -m mypy services
py -3.12 -m pytest -v
```
