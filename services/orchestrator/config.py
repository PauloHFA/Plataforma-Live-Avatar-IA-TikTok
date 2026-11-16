"""Configuração externa via variáveis de ambiente e perfis YAML (RF-18, RNF-05)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from services.models import GiftMatrixConfig, IdleConfig, PersonaProfile


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tiktok_unique_id: str = "@seu_usuario"
    live_source_mode: str = "simulator"

    # LLM
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model_fast: str = "gpt-4o-mini"
    llm_model_strong: str = "gpt-4o"
    llm_timeout_seconds: float = Field(default=6.0, ge=0.5, le=30.0)

    # Moderação
    moderation_enabled: bool = True
    moderation_api_url: str = ""
    moderation_api_key: str = ""

    # TTS
    tts_provider: str = "mock"
    tts_voice: str = "pt-BR-FranciscaNeural"
    tts_api_key: str = ""
    tts_region: str = "brazilsouth"
    tts_timeout_seconds: float = Field(default=4.0, ge=0.5, le=20.0)
    tts_cache_dir: Path = Path("./audio_cache")

    # Orçamento, retenção e segurança
    daily_budget_usd: float = Field(default=5.0, ge=0.0)
    operator_token: str = ""
    sqlite_path: Path = Path("./data/live_audit.db")
    retention_days: int = Field(default=30, ge=1)

    # Rede e OBS
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    obs_ws_url: str = "ws://127.0.0.1:4455"
    obs_ws_password: str = ""
    obs_contingency_scene: str = "Contingencia"
    alert_webhook_url: str = ""

    # Caminhos de configuração
    config_dir: Path = Path("./config")
    active_profile: str = "luna"

    @property
    def persona_path(self) -> Path:
        return self.config_dir / "profiles" / f"{self.active_profile}.yaml"

    @property
    def gifts_path(self) -> Path:
        return self.config_dir / "gifts.yaml"

    @property
    def banned_terms_path(self) -> Path:
        return self.config_dir / "banned_terms.txt"

    @property
    def idle_topics_path(self) -> Path:
        return self.config_dir / "idle_topics.yaml"


def load_persona_profile(path: Path) -> PersonaProfile:
    if not path.exists():
        return PersonaProfile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PersonaProfile.model_validate(raw)


def load_gift_matrix(path: Path) -> GiftMatrixConfig:
    if not path.exists():
        from services.models import GiftTierConfig, GiftTierId

        return GiftMatrixConfig(
            tiers=[
                GiftTierConfig(
                    id=GiftTierId.T1,
                    name="Pequeno",
                    min_coins=1,
                    max_coins=9,
                    fx="particles_light",
                    sound="chime_short",
                    gesture="wave",
                    use_llm=False,
                    cache_template="Valeu pelo presente {gift}, {user}!",
                ),
                GiftTierConfig(
                    id=GiftTierId.T5,
                    name="Lendario",
                    min_coins=10,
                    max_coins=9999999,
                    fx="cinematic_legendary",
                    sound="orchestra_hit",
                    gesture="bow_legendary",
                    interrupt=True,
                    alert_operator=True,
                ),
            ]
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GiftMatrixConfig.model_validate(raw)


def load_banned_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lower()
        if cleaned and not cleaned.startswith("#"):
            terms.append(cleaned)
    return terms


def load_idle_config(path: Path) -> IdleConfig:
    if not path.exists():
        return IdleConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IdleConfig.model_validate(raw)
