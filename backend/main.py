from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("PRISM starting up")
    yield
    logger.info("PRISM shutting down")


app = FastAPI(title="PRISM", version="0.1.0", lifespan=lifespan)

# Prod should set CORS_ALLOW_ORIGINS to the Vercel domain, e.g.:
#   CORS_ALLOW_ORIGINS=https://prism.vercel.app
# The CDK stack injects "*" by default; override via ECS environment after
# the Vercel URL is known, then `make redeploy`.
_raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "*")
_allow_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
# Credentials with a wildcard origin is unsafe (and the spec forbids it): browsers
# would attach cookies/Authorization to any origin. Only allow credentials once
# CORS_ALLOW_ORIGINS is scoped to explicit domains (the Vercel URL in prod).
_allow_credentials = _allow_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "prism"}

from api.connectors import router as connectors_router
app.include_router(connectors_router)

from api.pipeline import router as pipeline_router
app.include_router(pipeline_router)

from api.admin import router as admin_router
app.include_router(admin_router)

from api.insights import router as insights_router
app.include_router(insights_router)
