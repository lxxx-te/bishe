"""POST /api/ingest/run - manual ingestion trigger (demo/debug)."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.ingest import run_ingest

router = APIRouter()


@router.post("/ingest/run")
async def trigger_ingest() -> dict:
    return await run_ingest()