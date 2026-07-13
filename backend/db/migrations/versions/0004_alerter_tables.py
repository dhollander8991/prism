"""Alerter persistence: theme_trends table + spike columns on clusters

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-13

Creates:
- theme_trends (cluster_id, week, count) — weekly volume timeseries per theme.
  Primary key is (cluster_id, week); the alerter deletes-then-reinserts per cluster
  on each run so the table is idempotent.

Adds to clusters:
- spike_week           DATE nullable  — Monday of the most significant detected spike
- spike_z              FLOAT nullable — z-score of that spike (or display magnitude when
                                        baseline std was 0 — see alerter.py WIDE_MARGIN_FACTOR)
- spike_count          INTEGER nullable — raw item count for the spike week
- spike_baseline_mean  FLOAT nullable — mean of the n-week baseline preceding the spike
                                        (included so the detail API can return baseline_mean
                                         in its spike object without a separate join)
- has_sufficient_history  BOOLEAN NOT NULL default false — False means the cluster had
                          fewer than n+1 weeks of data; callers must NOT treat this as
                          "evaluated, no spike" — it means "not enough data to evaluate".

spike_baseline_mean is included beyond the task's minimum listed columns because the
detail API endpoint returns {"week","sigma","count","baseline_mean"} and needs the value
without a second query to the theme_trends table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # theme_trends: weekly item-count timeseries per canonical theme.
    op.create_table(
        "theme_trends",
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("cluster_id", "week"),
    )

    # Spike summary columns on clusters — nullable except has_sufficient_history.
    op.add_column("clusters", sa.Column("spike_week", sa.Date(), nullable=True))
    op.add_column("clusters", sa.Column("spike_z", sa.Float(), nullable=True))
    op.add_column("clusters", sa.Column("spike_count", sa.Integer(), nullable=True))
    op.add_column("clusters", sa.Column("spike_baseline_mean", sa.Float(), nullable=True))
    op.add_column(
        "clusters",
        sa.Column(
            "has_sufficient_history",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("clusters", "has_sufficient_history")
    op.drop_column("clusters", "spike_baseline_mean")
    op.drop_column("clusters", "spike_count")
    op.drop_column("clusters", "spike_z")
    op.drop_column("clusters", "spike_week")
    op.drop_table("theme_trends")
