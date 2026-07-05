"""pytest shared fixtures.

Reset the async SQLAlchemy engine between tests so pytest-asyncio's per-test
event loop doesn't carry dead connections from the previous test.
"""
from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_each_test():
    yield
    from app.db.session import engine
    await engine.dispose()