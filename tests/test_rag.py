"""Unit tests for P5 RAG answer stream.

Mocks the retrieval pipeline and the DeepSeek streaming client so tests run
without API calls or local embedding models.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import NewsEvent, NewsReport
from app.services.rag.answer import rag_answer_stream


@pytest.fixture(autouse=True)
def _fake_api_key():
    """RAG tests mock the LLM client; still need an API key to pass the guard."""
    fake_settings = MagicMock()
    fake_settings.deepseek_api_key = "fake-key"
    fake_settings.deepseek_model = "deepseek-chat"
    with patch("app.core.config.settings", fake_settings):
        yield


def _make_event(event_id: int) -> NewsEvent:
    return NewsEvent(
        id=event_id,
        merged_summary=f"事件 {event_id} 的合并摘要",
        embedding=None,
        keywords=["测试"],
        source_count=2,
        event_publish_time=datetime(2025, 6, 1),
        category="测试",
    )


def _make_report(report_id: int, event_id: int) -> NewsReport:
    return NewsReport(
        id=report_id,
        source_site="测试网",
        title=f"报道 {report_id}",
        raw_text=None,
        original_url=f"http://example.com/{report_id}",
        publish_time=datetime(2025, 6, 1),
        event_id=event_id,
    )


class _MockDelta:
    def __init__(self, content: str | None):
        self.content = content


class _MockChoice:
    def __init__(self, content: str | None):
        self.delta = _MockDelta(content)


class _MockChunk:
    def __init__(self, content: str | None):
        self.choices = [_MockChoice(content)]


def _mock_client_stream(contents: list[str | None]) -> MagicMock:
    """Return a mock AsyncOpenAI client whose chat.completions.create yields contents."""
    client = MagicMock()

    async def _stream():
        for c in contents:
            yield _MockChunk(c)

    create_coro = AsyncMock(return_value=_stream())
    client.chat.completions.create = create_coro
    return client


def _events_from_stream(events: list[str]) -> dict:
    """Parse SSE events into a dict of last-seen values by event type."""
    parsed = {}
    for evt in events:
        if evt.startswith("event: "):
            lines = evt.split("\n")
            kind = lines[0].replace("event: ", "")
            data = "\n".join(lines[1:]).replace("data: ", "", 1)
            parsed[kind] = data
    return parsed


@pytest.mark.asyncio
async def test_rag_empty_recall_refusal():
    """No retrieved events -> immediate refusal."""
    with patch("app.services.rag.answer.retrieve", new=AsyncMock(return_value=[])):
        events = [e async for e in rag_answer_stream(MagicMock(), "测试问题")]

    parsed = _events_from_stream(events)
    assert json.loads(parsed["done"])["refusal"] is True


@pytest.mark.asyncio
async def test_rag_normal_answer_with_valid_citation():
    """LLM answers with a citation inside the retrieved set."""
    event = _make_event(1)
    report = _make_report(101, 1)

    with patch("app.services.rag.answer.retrieve", new=AsyncMock(return_value=[1])):
        with patch(
            "app.services.rag.answer.fetch_events_with_reports",
            new=AsyncMock(return_value=[(event, [report])]),
        ):
            with patch(
                "app.services.rag.answer._get_client",
                return_value=_mock_client_stream(["这是答案", "[事件#1]。"]),
            ):
                events = [e async for e in rag_answer_stream(MagicMock(), "测试问题")]

    parsed = _events_from_stream(events)
    done = json.loads(parsed["done"])
    assert done["refusal"] is False
    assert done["citations"] == [1]
    assert done["citations_valid"] is True


@pytest.mark.asyncio
async def test_rag_refusal_when_llm_says_insufficient():
    """LLM outputs '信息不足' -> refusal=true."""
    event = _make_event(1)
    report = _make_report(101, 1)

    with patch("app.services.rag.answer.retrieve", new=AsyncMock(return_value=[1])):
        with patch(
            "app.services.rag.answer.fetch_events_with_reports",
            new=AsyncMock(return_value=[(event, [report])]),
        ):
            with patch(
                "app.services.rag.answer._get_client",
                return_value=_mock_client_stream(["信息不足"]),
            ):
                events = [e async for e in rag_answer_stream(MagicMock(), "测试问题")]

    parsed = _events_from_stream(events)
    done = json.loads(parsed["done"])
    assert done["refusal"] is True


@pytest.mark.asyncio
async def test_rag_invalid_citation_detected():
    """LLM cites an event id not in the retrieved set -> citations_valid=false."""
    event = _make_event(1)
    report = _make_report(101, 1)

    with patch("app.services.rag.answer.retrieve", new=AsyncMock(return_value=[1])):
        with patch(
            "app.services.rag.answer.fetch_events_with_reports",
            new=AsyncMock(return_value=[(event, [report])]),
        ):
            with patch(
                "app.services.rag.answer._get_client",
                return_value=_mock_client_stream(["答案[事件#999]。"]),
            ):
                events = [e async for e in rag_answer_stream(MagicMock(), "测试问题")]

    parsed = _events_from_stream(events)
    done = json.loads(parsed["done"])
    assert done["refusal"] is False
    assert done["citations"] == [999]
    assert done["citations_valid"] is False


@pytest.mark.asyncio
async def test_rag_exception_returns_error_and_refusal():
    """LLM call raises -> error=true and refusal=true."""
    event = _make_event(1)
    report = _make_report(101, 1)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("API 超时"))

    with patch("app.services.rag.answer.retrieve", new=AsyncMock(return_value=[1])):
        with patch(
            "app.services.rag.answer.fetch_events_with_reports",
            new=AsyncMock(return_value=[(event, [report])]),
        ):
            with patch("app.services.rag.answer._get_client", return_value=client):
                events = [e async for e in rag_answer_stream(MagicMock(), "测试问题")]

    parsed = _events_from_stream(events)
    done = json.loads(parsed["done"])
    assert done["error"] is True
    assert done["refusal"] is True
