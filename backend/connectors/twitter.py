from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector


class TwitterConnector(BaseConnector):
    SOURCE = "twitter"

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        # Needs: X_BEARER_TOKEN (OAuth2 app-only). Recent-search endpoint
        # GET /2/tweets/search/recent requires a paid tier as of 2023.
        raise NotImplementedError("Twitter/X connector requires an X API bearer token")
