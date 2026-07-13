from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# iTunes RSS — public, no auth, up to 50 reviews per page, 10 pages max.
_RSS_URL = (
    "https://itunes.apple.com/{country}/rss/customerreviews"
    "/page={page}/id={app_id}/sortBy=mostRecent/json"
)


class AppStoreConnector(BaseConnector):
    SOURCE = "app_store"

    def __init__(self, app_id: str, country: str = "us") -> None:
        self.app_id = app_id
        self.country = country

    def _parse_entry(self, entry: dict) -> dict | None:
        """Convert one raw RSS entry into our DB row shape. Returns None if unparseable."""
        try:
            review_id = entry.get("id", {}).get("label", "")
            title = entry.get("title", {}).get("label", "") or ""
            body = entry.get("content", {}).get("label", "") or ""
            stars = int(entry.get("im:rating", {}).get("label", 0) or 0)
            updated = entry.get("updated", {}).get("label", "")

            if not body or not review_id:
                return None

            try:
                created_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.now(timezone.utc)

            return {
                "id": str(uuid.uuid4()),
                "source": self.SOURCE,
                "source_id": self._source_id(f"{self.app_id}:{review_id}"),
                "text": f"{title} {body}".strip(),
                "modality": "text",
                "language": "en",
                "item_metadata": {
                    "stars": stars,
                    "title": title,
                    "app_id": self.app_id,
                    "country": self.country,
                },
                "created_at": created_at,
            }
        except Exception as exc:
            logger.warning("Skipping unparseable entry: %s", exc)
            return None

    @staticmethod
    def _entries(payload: dict) -> list[dict]:
        """Normalise feed.entry: missing -> [], single dict -> [dict], list -> list.
        Drops a leading app-metadata element (no im:rating)."""
        entry = payload.get("feed", {}).get("entry")
        if entry is None:
            return []
        entries = entry if isinstance(entry, list) else [entry]
        if entries and "im:rating" not in entries[0]:
            entries = entries[1:]
        return entries

    async def _fetch_page(self, client: httpx.AsyncClient, page: int) -> list[dict]:
        url = _RSS_URL.format(country=self.country, page=page, app_id=self.app_id)
        # Apple's RSS intermittently returns a feed with no `entry` key at all for a valid
        # app; a retry almost always fixes it. Retry a few times before giving up on the page.
        for attempt in range(3):
            try:
                r = await client.get(url, timeout=10.0)
                r.raise_for_status()
                entries = self._entries(r.json())
                if entries:
                    return entries
                logger.warning("Page %d empty (attempt %d/3), retrying", page, attempt + 1)
            except Exception as exc:
                logger.warning("Page %d fetch failed (attempt %d/3): %s", page, attempt + 1, exc)
        return []

    async def fetch_and_store(self, db: AsyncSession, count: int = 200) -> int:
        pages = min((count + 49) // 50, 10)  # 50 reviews/page, 10 pages max = 500 max

        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        ) as client:
            raw: list[dict] = []
            for page in range(1, pages + 1):
                entries = await self._fetch_page(client, page)
                if not entries:
                    break
                raw.extend(entries)
                if len(raw) >= count:
                    break

        parsed = [it for e in raw[:count] if (it := self._parse_entry(e))]
        logger.info(
            "app_store id=%s: %d raw entries -> %d parsed", self.app_id, len(raw), len(parsed)
        )
        return await self._store(db, parsed)
