"""创建分析上下文持久化表。

Revision ID: 20261028_0001
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261028_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    """创建会话、运行、检查点、审核、报告与事件表。"""

    op.create_table(
        "analysis_threads",
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("repository_name", sa.String(length=200), nullable=False),
        sa.Column("commit_ref", sa.String(length=200), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("workflow_status", sa.String(length=50), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=True),
        sa.Column(
            "api_version",
            sa.String(length=20),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index(
        "ix_analysis_threads_status_updated",
        "analysis_threads",
        ["workflow_status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "token_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["analysis_threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_analysis_runs_thread_id",
        "analysis_runs",
        ["thread_id"],
        unique=False,
    )

    op.create_table(
        "analysis_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("payload", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.run_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["analysis_threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_analysis_checkpoint_sequence",
        ),
    )
    op.create_index(
        "ix_analysis_checkpoints_thread_id",
        "analysis_checkpoints",
        ["thread_id"],
        unique=False,
    )

    op.create_table(
        "review_decisions",
        sa.Column("decision_id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column(
            "feedback",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["analysis_threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index(
        "ix_review_decisions_thread_id",
        "review_decisions",
        ["thread_id"],
        unique=False,
    )

    op.create_table(
        "analysis_reports",
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("report_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("report_digest", sa.String(length=80), nullable=False),
        sa.Column("payload", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["analysis_threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("report_id"),
        sa.UniqueConstraint("thread_id"),
    )

    op.create_table(
        "analysis_events",
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["analysis_threads.thread_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "thread_id",
            "sequence",
            name="uq_analysis_event_sequence",
        ),
    )
    op.create_index(
        "ix_analysis_events_thread_id",
        "analysis_events",
        ["thread_id"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系逆序删除上下文表。"""

    op.drop_index(
        "ix_analysis_events_thread_id",
        table_name="analysis_events",
    )
    op.drop_table("analysis_events")

    op.drop_table("analysis_reports")

    op.drop_index(
        "ix_review_decisions_thread_id",
        table_name="review_decisions",
    )
    op.drop_table("review_decisions")

    op.drop_index(
        "ix_analysis_checkpoints_thread_id",
        table_name="analysis_checkpoints",
    )
    op.drop_table("analysis_checkpoints")

    op.drop_index(
        "ix_analysis_runs_thread_id",
        table_name="analysis_runs",
    )
    op.drop_table("analysis_runs")

    op.drop_index(
        "ix_analysis_threads_status_updated",
        table_name="analysis_threads",
    )
    op.drop_table("analysis_threads")
