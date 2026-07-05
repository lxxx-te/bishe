"""POST /api/p2/run - trigger P2 (summarize + embed) on pending reports."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.p2 import run_p2

router = APIRouter()


@router.post("/p2/run")
async def trigger_p2(limit: int | None = Query(None, ge=1, le=2000)) -> dict:
    return await run_p2(limit=limit)