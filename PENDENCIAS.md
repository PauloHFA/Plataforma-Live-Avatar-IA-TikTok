# Pendências — Plataforma-Live-Avatar-IA-TikTok (`live-ia-tiktok`)

## 1. Implementado e Verificado

- **Contratos & Config (`services/models.py`, `config/`):**
  - Eventos normalizados (`chat`, `gift`, `follow`, `share`, `like`, `join`, `viewer_count`), comandos Godot (`speak`, `gift_fx`, `overlay`, `safe_mode`, `scene`), mensagens de retorno (`speech_started`, `speech_finished`, `heartbeat`, `error`), perfis YAML (`luna.yaml`), matriz de presentes (`gifts.yaml`), lista proibida (`banned_terms.txt`) e tópicos ociosos (`idle_topics.yaml`).
- **Ingestão (`services/ingest/`):**
  - Interface `LiveSource` com agregação de combos/streaks, backoff exponencial (`1s..60s`), `SimulatorSource` e adapter `TikTokLiveSource`.
- **Orquestrador (`services/orchestrator/`):**
  - Fila `P0..P5` com rate limit por usuário e preservação de presentes sob sobrecarga.
  - Moderação em múltiplas camadas (`rules`, `injection`, `classifier`) na entrada e na saída.
  - Cérebro LLM (`OpenAICompatibleLLMClient` + `RuleMockLLMClient`) com isolamento XML `<chat_message>`, timeout e fala de contingência.
  - TTS & Visemes (`MockSineTTSClient`, `AzureSpeechTTSClient`, `AudioCache`, fallback WAV).
  - Matriz de presentes T1–T5, minijogos (`!danca`, `!cor`, `!votar`), máquina de estados, controle de orçamento diário (`80%` alerta / `100%` modo econômico), métricas Prometheus (`/metrics`) e persistência SQLite com retenção/exclusão LGPD.
  - API FastAPI + Painel do Operador (`services/operator-panel/index.html`) com autenticação opcional por bearer token (`OPERATOR_TOKEN`).
- **Cliente Godot 4.x (`godot/`):**
  - Projeto `1080x1920`, layout de áudio `Voice` (`SpectrumAnalyzer`), WebSocket client, lip-sync híbrido (visemes + amplitude), emoções, `GiftFX`, `OverlayUI` (`Avatar de IA`) e `SafeMode`.
  - Validado via Godot 4.7.2 `--headless --quit` sem erros de parse ou cena.
- **Testes automatizados (`tests/`):**
  - Suíte unitária, integração (WebSocket real, FastAPI, 1.000 presentes, timeout fallback, kill switch), adversarial (240+ casos de injeção/spam/PII/toxicidade) e carga (500 eventos em rajada).

---

## 2. Melhorias Opcionais Futuras

- Substituir o avatar procedural 2D em `godot/scenes/avatar.tscn` por modelo 3D `.vrm`/`.gltf` com `AnimationTree` e blendshapes completos ou Live2D (`GDCubism`).
- Adicionar suporte nativo a ElevenLabs / Piper / Coqui XTTS local em `services/orchestrator/voice/` e testes automatizados GUT dentro de `godot/`.
- Integrar envio automático de alertas para webhook Discord/Telegram quando `ALERT_WEBHOOK_URL` estiver preenchido.

---

## 3. O que Depende do Usuário / Infraestrutura Externa

- **Conta TikTok elegível & `TikTokLive`:** verificar elegibilidade para LIVE/RTMP, instalar o extra `pip install ".[tiktok]"` e configurar `TIKTOK_UNIQUE_ID` e `LIVE_SOURCE_MODE=tiktok` (ou `obs` via `pip install ".[obs]"`).
- **Chaves de API reais (`.env`):** copiar `.env.example` para `.env` e preencher `LLM_API_KEY`, `TTS_API_KEY`, `OPERATOR_TOKEN` e `OBS_WS_PASSWORD` (nunca versionar `.env`).
- **OBS Studio & áudio virtual:** configurar captura de janela da Godot em `1080x1920` no OBS, áudio dedicado do bus `Voice` e servidor RTMP do TikTok LIVE para live piloto real (`CA-20`).
