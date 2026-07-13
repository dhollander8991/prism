from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FeedbackItemORM

logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """Shared dedup + bulk-store logic. Subclasses only map their source to item dicts."""

    SOURCE: str = ""

    def _source_id(self, raw_id: str) -> str:
        # Deterministic hash so the same item never gets inserted twice.
        return hashlib.sha256(f"{self.SOURCE}:{raw_id}".encode()).hexdigest()[:24]

    async def _exists(self, db: AsyncSession, source_id: str) -> bool:
        r = await db.execute(
            select(FeedbackItemORM.source_id).where(FeedbackItemORM.source_id == source_id)
        )
        return r.scalar_one_or_none() is not None

    async def _store(self, db: AsyncSession, items: list[dict]) -> int:
        """Insert items, skipping any whose source_id already exists in the DB or the batch."""
        if not items:
            await db.commit()
            return 0

        # Dedup within the batch first (same item can appear twice in one fetch).
        unique: dict[str, dict] = {}
        for it in items:
            unique.setdefault(it["source_id"], it)

        # ponytail: single IN() lookup; fine for our per-sync counts (<=500). Chunk if that grows.
        existing = await db.execute(
            select(FeedbackItemORM.source_id).where(
                FeedbackItemORM.source_id.in_(list(unique))
            )
        )
        have = {row[0] for row in existing.all()}
        new = [it for sid, it in unique.items() if sid not in have]

        db.add_all(FeedbackItemORM(**it) for it in new)
        await db.commit()
        logger.info(
            "%s: stored %d new / skipped %d duplicate(s)",
            self.SOURCE,
            len(new),
            len(items) - len(new),
        )
        return len(new)

    @abstractmethod
    async def fetch_and_store(self, db: AsyncSession, count: int) -> int: ...
