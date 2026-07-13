from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# Reddit serves JSON for any listing by appending .json. Read-only, no auth.
# NOTE: Reddit 403s unauthenticated requests from many datacenter IPs; works from
# residential IPs / where Reddit allows it. Swap in OAuth if you need it server-side.
_UA = "prism-feedback/0.1 (product-feedback intelligence)"


class RedditConnector(BaseConnector):
    SOURCE = "reddit"

    def __init__(self, subreddit: str, time_filter: str = "week") -> None:
        self.subreddit = subreddit
        self.time_filter = time_filter  # hour/day/week/month/year/all

    def _map(self, kind: str, d: dict) -> dict | None:
        rid = d.get("id") or ""
        if kind == "t3":  # post: title + optional selftext
            body = f"{d.get('title', '')} {d.get('selftext', '') or ''}".strip()
        else:  # t1 comment
            body = (d.get("body") or "").strip()
        if not body or not rid:
            return None

        created = d.get("created_utc")
        created_at = (
            datetime.fromtimestamp(created, tz=timezone.utc)
            if created
            else datetime.now(timezone.utc)
        )
        return {
            "id": str(uuid.uuid4()),
            "source": self.SOURCE,
            "source_id": self._source_id(f"{kind}:{rid}"),
            "text": body,
            "modality": "text",
            "language": "en",
            "item_metadata": {
                "subreddit": self.subreddit,
                "kind": "post" if kind == "t3" else "comment",
                "author": d.get("author"),
                "score": d.get("score"),
                "permalink": f"https://reddit.com{d.get('permalink', '')}",
            },
            "created_at": created_at,
        }

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        items: list[dict] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA}, follow_redirects=True, timeout=10.0
        ) as client:
            r = await client.get(
                f"https://www.reddit.com/r/{self.subreddit}/top.json",
                params={"t": self.time_filter, "limit": min(count, 100)},
            )
            r.raise_for_status()
            posts = r.json().get("data", {}).get("children", [])

            for p in posts:
                if len(items) >= count:
                    break
                d = p.get("data", {})
                if it := self._map("t3", d):
                    items.append(it)
                # Pull this post's top-level comments too, up to the remaining budget.
                if len(items) >= count:
                    break
                cr = await client.get(
                    f"https://www.reddit.com/r/{self.subreddit}/comments/{d.get('id')}.json",
                    params={"limit": 20},
                )
                if cr.status_code != 200:
                    continue
                listings = cr.json()
                children = listings[1].get("data", {}).get("children", []) if len(listings) > 1 else []
                for c in children:
                    if len(items) >= count:
                        break
                    if c.get("kind") == "t1" and (it := self._map("t1", c.get("data", {}))):
                        items.append(it)

        logger.info("reddit r/%s: %d items mapped", self.subreddit, len(items))
        return await self._store(db, items)
