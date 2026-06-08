from __future__ import annotations
import os
import yaml
from pydantic import BaseModel, ConfigDict, Field


class ChannelConfig(BaseModel):
    slug: str
    username: str | None = None
    id: int | None = None
    priority: float = 1.0
    custom_prompt: str | None = None


class TelegramConfig(BaseModel):
    api_id: int
    api_hash: str
    session_name: str = "briefing_session"
    channels: list[ChannelConfig]
    rate_limit_delay_ms: int = 500
    lookback_seconds: int = 604800  # how far back to fetch on first run (default: 1 week)


class LMStudioConfig(BaseModel):
    model: str
    server_host: str = "localhost"
    server_port: int = 1234
    api_token: str | None = None
    temperature: float = 0.3
    max_tokens: int = 800
    max_concurrent_analyses: int = 1


class TriageConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    keyword_boost: float = 0.5
    min_composite_score: float = 2.5
    max_main_items: int = 50
    dedup_window_secs: int = 7200
    entity_cluster_window_secs: int = 86400
    categories_of_interest: list[str] = Field(
        default_factory=lambda: ["Breaking News", "Official Statement", "Analysis"]
    )


class GenerationConfig(BaseModel):
    output_dir: str = "./briefings"
    timezone: str = "UTC"
    generate_at: str = "23:59"
    include_raw_text: bool = False
    include_all_images: bool = True


class StorageConfig(BaseModel):
    db_path: str = "./data/briefing.db"
    media_dir: str = "./data/media"
    retention_days: int = 30


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram: TelegramConfig
    lmstudio: LMStudioConfig
    triage: TriageConfig = Field(default_factory=TriageConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


def load_config(path: str, env_override: bool = False) -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    if env_override:
        if api_id := os.getenv("TG_API_ID"):
            try:
                data.setdefault("telegram", {})["api_id"] = int(api_id)
            except ValueError:
                raise ValueError(f"TG_API_ID env var must be an integer, got: {api_id!r}")
        if api_hash := os.getenv("TG_API_HASH"):
            data.setdefault("telegram", {})["api_hash"] = api_hash
        if lm_token := os.getenv("LM_API_TOKEN"):
            data.setdefault("lmstudio", {})["api_token"] = lm_token
    return AppConfig.model_validate(data)
