from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector


class ZendeskConnector(BaseConnector):
    SOURCE = "zendesk"

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        # Needs: ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN (basic auth
        # {email}/token:{token}). Pull from GET /api/v2/tickets.json or the Search API.
        raise NotImplementedError("Zendesk connector requires Zendesk API credentials")
