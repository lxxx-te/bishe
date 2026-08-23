"""User profile endpoints for onboarding interest tags."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import UserProfile

router = APIRouter()


class UserProfileIn(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    interest_tags: list[str] = Field(default_factory=list, max_length=8)


class UserProfileOut(BaseModel):
    user_id: str
    interest_tags: list[str]


@router.post("/user/profile")
async def upsert_profile(
    body: UserProfileIn,
    session: AsyncSession = Depends(get_session),
) -> UserProfileOut:
    """Create or update a user's explicit interest tags (no behavior tracking)."""
    row = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == body.user_id))
    ).scalar_one_or_none()

    if row is None:
        row = UserProfile(user_id=body.user_id, interest_tags=body.interest_tags)
        session.add(row)
    else:
        row.interest_tags = body.interest_tags

    await session.commit()
    return UserProfileOut(user_id=row.user_id, interest_tags=list(row.interest_tags or []))


@router.get("/user/profile/{user_id}")
async def get_profile(
    user_id: str,
    session: AsyncSession = Depends(get_session),
) -> UserProfileOut:
    row = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="user profile not found")
    return UserProfileOut(user_id=row.user_id, interest_tags=list(row.interest_tags or []))
