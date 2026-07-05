"""FastAPI entrypoint.

Run (after pip install):
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, ingest, p2, reports
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="多源新闻事件的向量聚合与检索系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(p2.router, prefix="/api")


@app.get("/")
async def root() -> dict:
    return {"name": "news-event-rag", "docs": "/docs"}