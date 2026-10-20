"""Add asynchronous script generation.

Revision ID: 0002_script_generation
Revises: 0001_articles
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_script_generation"
down_revision = "0001_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("article_id", sa.Uuid(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), sa.ForeignKey("script_versions.id")),
        sa.Column("target_language", sa.String(2), nullable=False),
        sa.Column("generation_config", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("requested_model", sa.String(200), nullable=False),
        sa.Column("actual_model", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('generated', 'edited')", name="ck_scripts_source"),
        sa.CheckConstraint("target_language IN ('en', 'de')", name="ck_scripts_language"),
    )
    op.create_index("ux_scripts_article_version", "script_versions", ["article_id", "version"], unique=True)
    op.create_index("ix_scripts_created_at", "script_versions", ["created_at"])

    op.create_table(
        "production_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("article_id", sa.Uuid(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("source_script_version_id", sa.Uuid(), sa.ForeignKey("script_versions.id")),
        sa.Column("result_script_version_id", sa.Uuid(), sa.ForeignKey("script_versions.id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("job_type = 'script_generation'", name="ck_jobs_type"),
        sa.CheckConstraint("status IN ('queued', 'running', 'retry_scheduled', 'succeeded', 'failed', 'needs_attention')", name="ck_jobs_status"),
    )
    op.create_index("ux_jobs_idempotency_key", "production_jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_jobs_article_created", "production_jobs", ["article_id", "created_at"])

    op.create_table(
        "task_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False),
        sa.Column("task_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'dispatched')", name="ck_outbox_status"),
    )
    op.create_index("ux_outbox_job", "task_outbox", ["job_id"], unique=True)
    op.create_index("ix_outbox_due", "task_outbox", ["status", "available_at"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_job_events_job_created", "job_events", ["job_id", "created_at"])

    op.create_table(
        "provider_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("requested_model", sa.String(200), nullable=False),
        sa.Column("actual_model", sa.String(200)),
        sa.Column("provider_request_id", sa.String(200)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("cost", sa.Numeric(14, 8)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_provider_calls_job", "provider_calls", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_table("provider_calls")
    op.drop_table("job_events")
    op.drop_table("task_outbox")
    op.drop_table("production_jobs")
    op.drop_table("script_versions")
