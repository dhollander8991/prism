from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from google_play_scraper import Sort, reviews
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)


class GooglePlayConnector(BaseConnector):
    SOURCE = "google_play"

    def __init__(self, app_id: str, country: str = "us", lang: str = "en") -> None:
        self.app_id = app_id  # e.g. "com.instagram.android"
        self.country = country
        self.lang = lang

    def _parse(self, r: dict) -> dict | None:
        body = (r.get("content") or "").strip()
        review_id = r.get("reviewId") or ""
        if not body or not review_id:
            return None

        at = r.get("at")
        created_at = at if isinstance(at, datetime) else datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return {
            "id": str(uuid.uuid4()),
            "source": self.SOURCE,
            "source_id": self._source_id(f"{self.app_id}:{review_id}"),
            "text": body,
            "modality": "text",
            "language": self.lang,
            "item_metadata": {
                "stars": r.get("score"),
                "app_id": self.app_id,
                "country": self.country,
                "app_version": r.get("reviewCreatedVersion"),
            },
            "created_at": created_at,
        }

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        # google-play-scraper is sync (stdlib urllib, zero deps) — run off the event loop.
        result, _ = await asyncio.to_thread(
            reviews,
            self.app_id,
            count=count,
            sort=Sort.NEWEST,
            lang=self.lang,
            country=self.country,
        )
        parsed = [it for r in result if (it := self._parse(r))]
        logger.info(
            "google_play id=%s: %d raw -> %d parsed", self.app_id, len(result), len(parsed)
        )
        return await self._store(db, parsed)
