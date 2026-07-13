from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/reset-feedback")
async def reset_feedback(confirm: bool = False, db: AsyncSession = Depends(get_db)) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to truncate feedback_items")
    # TRUNCATE clears rows + all cluster state (cluster_id/embedding live on this table).
    await db.execute(text("TRUNCATE TABLE feedback_items"))
    await db.commit()
    logger.info("admin: feedback_items truncated")
    return {"status": "ok", "truncated": "feedback_items"}
