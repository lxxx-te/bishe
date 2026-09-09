"""SSE endpoint POST /api/rag/ask - streamed RAG answer."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import UserProfile
from app.services.rag.answer import rag_answer_stream
from app.services.rag.dailylimit import today_limit_reached, increment_today_async

router = APIRouter()


@router.post("/rag/ask")
async def rag_ask(
    body: dict,
    user_id: str | None = Query(None, description="optional user_id for interest-tag filtering"),
) -> StreamingResponse:
    """Streamed RAG answer for a user question.

    Body: { "query": "最近三天关于X的新闻" }
    Query: ?user_id=xxx (optional, for interest tag pre-filter)
    Response: SSE events (event: meta / token / done)
    """
    query = (body or {}).get("query", "").strip()
    if not query:
        return StreamingResponse(
            iter([
                "event: token\ndata: 请输入问题\n\n",
                "event: done\ndata: {\"refusal\": true}\n\n",
                "event: eof\ndata: [DONE]\n\n",
            ]),
            media_type="text/event-stream",
        )

    if today_limit_reached():
        return StreamingResponse(
            iter([
                "event: token\ndata: 今日 RAG 查询额度已用尽，请明天再试\n\n",
                "event: done\ndata: {\"refusal\": true, \"reason\": \"daily_limit\"}\n\n",
                "event: eof\ndata: [DONE]\n\n",
            ]),
            media_type="text/event-stream",
        )

    # Load user interest tags if user_id given
    interest_tags: list[str] | None = None
    if user_id:
        async with AsyncSessionLocal() as s:
            row = (
                await s.execute(
                    select(UserProfile).where(UserProfile.user_id == user_id)
                )
            ).scalar_one_or_none()
            if row and row.interest_tags:
                interest_tags = list(row.interest_tags)

    async def gen():
        async with AsyncSessionLocal() as session:
            await increment_today_async(1)
            try:
                async for evt in rag_answer_stream(
                    session, query, interest_tags=interest_tags,
                    use_full_text=settings.rag_use_full_text,
                ):
                    yield evt
            except Exception:
                yield "event: done\ndata: {\"refusal\": true, \"error\": true}\n\n"
            finally:
                yield "event: eof\ndata: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")