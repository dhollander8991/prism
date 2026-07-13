from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.app_store import AppStoreConnector
from connectors.google_play import GooglePlayConnector
from connectors.hackernews import HackerNewsConnector
from connectors.reddit import RedditConnector
from db.database import get_db
from db.models import FeedbackItemORM

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["connectors"])


async def _run(connector, db: AsyncSession, count: int, **meta) -> dict:
    try:
        stored = await connector.fetch_and_store(db, count=count)
        return {"status": "ok", "stored": stored, **meta}
    except Exception as exc:
        logger.error("%s sync failed: %s", connector.SOURCE, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/connectors/app-store/sync")
async def sync_app_store(
    app_id: str,
    country: str = "us",
    count: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _run(AppStoreConnector(app_id, country), db, count, app_id=app_id)


@router.post("/connectors/google-play/sync")
async def sync_google_play(
    app_id: str,
    country: str = "us",
    lang: str = "en",
    count: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _run(GooglePlayConnector(app_id, country, lang), db, count, app_id=app_id)


@router.post("/connectors/hackernews/sync")
async def sync_hackernews(
    query: str,
    tags: str = "comment",
    count: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _run(HackerNewsConnector(query, tags), db, count, query=query)


@router.post("/connectors/reddit/sync")
async def sync_reddit(
    subreddit: str,
    time_filter: str = "week",
    count: int = 100,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _run(RedditConnector(subreddit, time_filter), db, count, subreddit=subreddit)


@router.get("/feedback")
async def list_feedback(
    limit: int = Query(50, le=200),
    offset: int = 0,
    source: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(FeedbackItemORM).order_by(FeedbackItemORM.created_at.desc())
    if source:
        q = q.where(FeedbackItemORM.source == source)
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "source": r.source,
                "text": r.text,
                "modality": r.modality,
                "language": r.language,
                "metadata": r.item_metadata,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
