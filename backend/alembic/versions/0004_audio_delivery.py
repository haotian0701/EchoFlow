"""Add audio delivery, accounting and request aliases.

Revision ID: 0004_audio_delivery
Revises: 0003_evaluation_review
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_audio_delivery"
down_revision = "0003_evaluation_review"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_jobs_type", "production_jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "production_jobs",
        "job_type IN ('script_generation', 'script_evaluation', 'audio_generation')",
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "article_id", sa.Uuid(), sa.ForeignKey("articles.id"), nullable=False
        ),
        sa.Column(
            "script_version_id",
            sa.Uuid(),
            sa.ForeignKey("script_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "approval_id", sa.Uuid(), sa.ForeignKey("approvals.id"), nullable=False
        ),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("voice_id", sa.String(200), nullable=False),
        sa.Column("voice_name", sa.String(200), nullable=False),
        sa.Column("speed", sa.Numeric(4, 2), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("object_key", sa.String(200), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(12, 3), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("input_characters", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("delete_error", sa.Text()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('available', 'deleting', 'delete_failed', 'deleted')",
            name="ck_audio_status",
        ),
        sa.CheckConstraint("language IN ('en', 'de')", name="ck_audio_language"),
        sa.CheckConstraint("speed >= 0.7 AND speed <= 1.2", name="ck_audio_speed"),
    )
    op.create_index("ux_audio_job", "audio_assets", ["job_id"], unique=True)
    op.create_index("ix_audio_created", "audio_assets", ["created_at"])
    op.create_index("ix_audio_script", "audio_assets", ["script_version_id"])
    op.add_column("production_jobs", sa.Column("result_audio_asset_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_jobs_result_audio",
        "production_jobs",
        "audio_assets",
        ["result_audio_asset_id"],
        ["id"],
    )
    op.create_table(
        "audio_requests",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False
        ),
    )
    op.add_column("provider_calls", sa.Column("input_characters", sa.Integer()))
    op.add_column("provider_calls", sa.Column("provider_credits", sa.Numeric(14, 4)))
    op.add_column(
        "provider_calls", sa.Column("audio_duration_seconds", sa.Numeric(12, 3))
    )


def downgrade():
    for name in ("audio_duration_seconds", "provider_credits", "input_characters"):
        op.drop_column("provider_calls", name)
    op.drop_table("audio_requests")
    op.drop_constraint("fk_jobs_result_audio", "production_jobs", type_="foreignkey")
    op.drop_column("production_jobs", "result_audio_asset_id")
    op.drop_table("audio_assets")
    # Downgrade requires removing Stage 4 jobs explicitly first.
    op.drop_constraint("ck_jobs_type", "production_jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "production_jobs",
        "job_type IN ('script_generation', 'script_evaluation')",
    )
