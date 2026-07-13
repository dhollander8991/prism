from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: Literal[
        "app_store", "google_play", "hackernews", "reddit",
        "zendesk", "twitter", "intercom", "g2", "typeform",
    ]
    source_id: str
    text: str
    modality: Literal["text", "audio", "image"]
    language: str = "en"
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    embedding: list[float] | None = None
    cluster_id: str | None = None
    processed: bool = False


class PipelineState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: str
    current_agent: str
    status: Literal["pending", "processing", "complete", "failed"]
    error: str | None = None
    started_at: datetime
    updated_at: datetime


class InsightReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    cluster_id: str
    title: str
    priority: Literal["P0", "P1", "P2", "P3"]
    findings: list[str]
    actions: list[str]
    item_count: int
    generated_at: datetime
