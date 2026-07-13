"""Extend insight_reports with synthesis-quality columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12

Adds the structured output columns that synthesiser_node now writes:
- priority_rationale   — why this priority was assigned
- recommended_actions  — JSONB array of {action, urgency} dicts
- affected_surface     — free-text surface area (nullable)
- churn_risk           — categorical 'high'|'medium'|'low'|'none'
- churn_rationale      — explanation (nullable)

The existing `actions` (JSON) column is left intact for back-compat;
`recommended_actions` is the new structured column.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "insight_reports",
        sa.Column("priority_rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "insight_reports",
        sa.Column(
            "recommended_actions",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "insight_reports",
        sa.Column("affected_surface", sa.String(), nullable=True),
    )
    op.add_column(
        "insight_reports",
        sa.Column("churn_risk", sa.String(10), nullable=True),
    )
    op.add_column(
        "insight_reports",
        sa.Column("churn_rationale", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("insight_reports", "churn_rationale")
    op.drop_column("insight_reports", "churn_risk")
    op.drop_column("insight_reports", "affected_surface")
    op.drop_column("insight_reports", "recommended_actions")
    op.drop_column("insight_reports", "priority_rationale")
