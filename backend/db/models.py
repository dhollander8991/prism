from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class FeedbackItemORM(Base):
    __tablename__ = "feedback_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str] = mapped_column(String(10), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, server_default="en")
    item_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class ClusterORM(Base):
    __tablename__ = "clusters"

    cluster_id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Alerter spike columns — populated by alerter_node; null until first run.
    # spike_baseline_mean stored here (not only in theme_trends) so the detail API
    # can return the full spike object in a single join without querying theme_trends.
    spike_week: Mapped[date | None] = mapped_column(Date, nullable=True)
    spike_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    spike_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spike_baseline_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_sufficient_history: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class ThemeTrendORM(Base):
    """Weekly item-count timeseries per canonical theme.

    Written idempotently by alerter_node: existing rows for a cluster are deleted
    before re-inserting so re-runs produce the same final state.
    """
    __tablename__ = "theme_trends"

    cluster_id: Mapped[str] = mapped_column(String, primary_key=True)
    week: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class PipelineStateORM(Base):
    __tablename__ = "pipeline_state"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    current_agent: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InsightReportORM(Base):
    __tablename__ = "insight_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cluster_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String(5), nullable=False)
    findings: Mapped[list] = mapped_column(JSON, nullable=False)
    # `actions` kept for back-compat; `recommended_actions` is the new structured column
    actions: Mapped[list] = mapped_column(JSON, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Extended columns added in migration 0003
    priority_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_actions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    affected_surface: Mapped[str | None] = mapped_column(String, nullable=True)
    churn_risk: Mapped[str | None] = mapped_column(String(10), nullable=True)
    churn_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
