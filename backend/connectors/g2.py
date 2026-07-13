from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import BaseConnector


class G2Connector(BaseConnector):
    SOURCE = "g2"

    async def fetch_and_store(self, db: AsyncSession, count: int = 100) -> int:
        # Needs: G2_API_TOKEN (partner program only — G2's review API is gated and not
        # publicly self-serve). Reviews come from the /products/{id}/reviews endpoint.
        raise NotImplementedError("G2 connector requires G2 partner API access")
