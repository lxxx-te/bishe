"""POST /api/p4/run - trigger P4 (5W1H extraction + event merge + re-embed)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.p4 import run_p4

router = APIRouter()


@router.post("/p4/run")
async def trigger_p4(limit_events: int | None = Query(None, ge=1, le=2000)) -> dict:
    return await run_p4(limit_events=limit_events)