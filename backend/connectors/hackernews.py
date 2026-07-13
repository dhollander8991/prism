from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://hn.algolia.com/api/v1/search"


class HackerNewsConnector(BaseConnector):
    SOURCE = "hackernews"

    def __init__(self, query: str, tags: str = "comment") -> None:
        self.query = query
        self.tags = tags  # "comment", "story", or "comment,story"

    def _parse(self, hit: dict) -> dict | None:
        object_id = hit.get("objectID") or ""
        # Comments carry comment_text; stories carry title.
        body = (hit.get("comment_text") or hit.get("title") or hit.get("story_title") or "").strip()
        if not body or not object_id:
            return None

        ts = hit.get("created_at_i")
        if ts is not None:
            created_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            try:
                created_at = datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.now(timezone.utc)

        return {
            "id": str(uuid.uuid4()),
            "source": self.SOURCE,
            "source_id": self._source_id(object_id),
            "text": body,
            "modality": "text",
            "language": "en",
            "item_metadata": {
                "query": self.query,
                "author": hit.get("author"),
                "points": hit.get("points"),
                "story_title": hit.get("story_title"),
                "url": f"https://news.ycombinator.com/item?id={object_id}",
            },
            "created_at": created_at,
        }

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        params = {"query": self.query, "tags": self.tags, "hitsPerPage": min(count, 1000)}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_SEARCH_URL, params=params)
            r.raise_for_status()
            hits = r.json().get("hits", [])

        parsed = [it for h in hits[:count] if (it := self._parse(h))]
        logger.info("hackernews q=%r: %d hits -> %d parsed", self.query, len(hits), len(parsed))
        return await self._store(db, parsed)
