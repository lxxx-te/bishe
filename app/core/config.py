"""Application configuration loaded from env / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Database
    db_url: str = Field(
        default="postgresql+asyncpg://news:changeme@localhost:5432/news_aggregator"
    )

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_rag_daily_limit: int = 200

    # NewsAPI
    newsapi_api_key: str = ""

    # Local models
    embedding_model: str = "BAAI/bge-small-zh"
    reranker_model: str = "BAAI/bge-reranker-base"

    # Dedup
    dedup_sim_threshold: float = 0.90
    dedup_precision_target: float = 0.9

    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()