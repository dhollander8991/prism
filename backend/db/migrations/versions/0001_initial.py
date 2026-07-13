"""initial

Revision ID: 0001
Revises:
Create Date: 2026-06-02

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "feedback_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("modality", sa.String(10), nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("cluster_id", sa.String(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "pipeline_state",
        sa.Column("item_id", sa.String(), primary_key=True),
        sa.Column("current_agent", sa.String(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "insight_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("priority", sa.String(5), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insight_reports")
    op.drop_table("pipeline_state")
    op.drop_table("feedback_items")
    op.execute("DROP EXTENSION IF EXISTS vector")
