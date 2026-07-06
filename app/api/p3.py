"""POST /api/p3/run - trigger P3 event dedup on pending reports."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.dedup import run_p3

router = APIRouter()


@router.post("/p3/run")
async def trigger_p3(limit: int | None = Query(None, ge=1, le=2000)) -> dict:
    return await run_p3(limit=limit)