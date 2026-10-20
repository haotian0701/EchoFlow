"""Add script evaluation and human review.

Revision ID: 0003_evaluation_review
Revises: 0002_script_generation
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_evaluation_review"
down_revision = "0002_script_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_jobs_type", "production_jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "production_jobs",
        "job_type IN ('script_generation', 'script_evaluation')",
    )

    op.add_column(
        "task_outbox",
        sa.Column(
            "queue_name",
            sa.String(64),
            nullable=False,
            server_default="script_generation",
        ),
    )
    op.add_column(
        "provider_calls",
        sa.Column(
            "operation",
            sa.String(32),
            nullable=False,
            server_default="script_generation",
        ),
    )

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "script_version_id",
            sa.Uuid(),
            sa.ForeignKey("script_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("production_jobs.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("factual_consistency", sa.Numeric(4, 3)),
        sa.Column("content_coverage", sa.Numeric(4, 3)),
        sa.Column("audio_readiness", sa.Numeric(4, 3)),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32)),
        sa.Column("requested_model", sa.String(200)),
        sa.Column("actual_model", sa.String(200)),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_attention')",
            name="ck_evaluations_status",
        ),
        sa.CheckConstraint(
            "factual_consistency IS NULL OR (factual_consistency >= 0 AND factual_consistency <= 1)",
            name="ck_evaluations_factual_score",
        ),
        sa.CheckConstraint(
            "content_coverage IS NULL OR (content_coverage >= 0 AND content_coverage <= 1)",
            name="ck_evaluations_coverage_score",
        ),
        sa.CheckConstraint(
            "audio_readiness IS NULL OR (audio_readiness >= 0 AND audio_readiness <= 1)",
            name="ck_evaluations_audio_score",
        ),
    )
    op.create_index("ux_evaluations_job", "evaluations", ["job_id"], unique=True)
    op.create_index(
        "ix_evaluations_script_created",
        "evaluations",
        ["script_version_id", "created_at"],
    )

    op.add_column("production_jobs", sa.Column("result_evaluation_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_jobs_result_evaluation",
        "production_jobs",
        "evaluations",
        ["result_evaluation_id"],
        ["id"],
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "script_version_id",
            sa.Uuid(),
            sa.ForeignKey("script_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_id",
            sa.Uuid(),
            sa.ForeignKey("evaluations.id"),
            nullable=False,
        ),
        sa.Column(
            "acknowledged_critical_issue_ids", sa.JSON(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ux_approvals_evaluation", "approvals", ["evaluation_id"], unique=True
    )
    op.create_index(
        "ix_approvals_script_created",
        "approvals",
        ["script_version_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_constraint(
        "fk_jobs_result_evaluation", "production_jobs", type_="foreignkey"
    )
    op.drop_column("production_jobs", "result_evaluation_id")
    op.drop_table("evaluations")
    op.drop_column("provider_calls", "operation")
    op.drop_column("task_outbox", "queue_name")
    op.drop_constraint("ck_jobs_type", "production_jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type", "production_jobs", "job_type = 'script_generation'"
    )
