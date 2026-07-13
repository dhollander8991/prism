from __future__ import annotations

import logging

from sqlalchemy import select

from agents.state import PipelineState
from db.database import AsyncSessionFactory
from db.models import FeedbackItemORM

logger = logging.getLogger(__name__)


async def ingestor_node(state: PipelineState) -> dict:
    ids = state.get("item_ids", [])
    if not ids:
        return {"texts": {}}

    async with AsyncSessionFactory() as db:
        rows = await db.execute(
            select(FeedbackItemORM.id, FeedbackItemORM.text).where(
                FeedbackItemORM.id.in_(ids)
            )
        )
        texts = {rid: text for rid, text in rows.all()}

    # TODO: modality routing. audio -> Whisper (ASR), image -> BLIP-2 (image-to-text),
    # foreign-language -> NLLB translation. For now every item is text and passes straight
    # through unchanged.
    logger.info("ingestor: loaded %d/%d texts", len(texts), len(ids))
    return {"texts": texts}
