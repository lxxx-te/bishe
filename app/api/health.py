"""/api/health - liveness + config probe."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "newsapi_configured": bool(settings.newsapi_api_key),
        "embedding_model": settings.embedding_model,
    }