from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector


class IntercomConnector(BaseConnector):
    SOURCE = "intercom"

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        # Needs: INTERCOM_ACCESS_TOKEN (Bearer). Pull conversations from
        # POST /conversations/search; each conversation_part is a feedback item.
        raise NotImplementedError("Intercom connector requires an Intercom access token")
