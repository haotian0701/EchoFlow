"""Execution leases, retry chains, durable audio and maintenance heartbeat."""

import sqlalchemy as sa

from alembic import op

revision = "0005_recovery"
down_revision = "0004_audio_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "production_jobs",
        sa.Column("retry_of_job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id")),
    )
    op.create_unique_constraint(
        "ux_jobs_retry_of", "production_jobs", ["retry_of_job_id"]
    )
    op.add_column(
        "production_jobs",
        sa.Column("root_job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id")),
    )
    op.add_column(
        "production_jobs",
        sa.Column("provider_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "job_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','abandoned')",
            name="ck_execution_status",
        ),
    )
    op.create_index(
        "ux_execution_attempt", "job_executions", ["job_id", "attempt_no"], unique=True
    )
    op.create_index(
        "ix_execution_lease", "job_executions", ["status", "lease_expires_at"]
    )
    op.add_column(
        "production_jobs",
        sa.Column(
            "current_execution_id",
            sa.Uuid(),
            sa.ForeignKey("job_executions.id", name="fk_jobs_execution"),
        ),
    )
    op.add_column(
        "provider_calls",
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("job_executions.id")),
    )
    op.add_column(
        "provider_calls",
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "provider_calls",
        sa.Column("uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "audio_assets",
        sa.Column("delete_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "job_retry_requests",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False
        ),
    )
    op.create_table(
        "maintenance_heartbeats",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audio_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("production_jobs.id"), nullable=False
        ),
        sa.Column(
            "provider_call_id",
            sa.Uuid(),
            sa.ForeignKey("provider_calls.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "approval_id", sa.Uuid(), sa.ForeignKey("approvals.id"), nullable=False
        ),
        sa.Column("audio_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("staging_path", sa.String(300), nullable=False),
        sa.Column("object_key", sa.String(200), unique=True, nullable=False),
        sa.Column("bucket", sa.String(100), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("duration_seconds", sa.Numeric(12, 3)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(300)),
    )
    op.create_index("ix_audio_artifacts_job_id", "audio_artifacts", ["job_id"])
    op.execute("UPDATE production_jobs SET root_job_id = id")
    # Legacy in-flight work has no trustworthy fencing token or charging evidence.
    op.execute(
        "UPDATE evaluations SET status='needs_attention', completed_at=now() WHERE job_id IN (SELECT id FROM production_jobs WHERE status='running')"
    )
    op.execute(
        "UPDATE provider_calls SET status='unknown', uncertain=true, error_code='LEGACY_EXECUTION_UNKNOWN' WHERE job_id IN (SELECT id FROM production_jobs WHERE status='running') AND status='running'"
    )
    op.execute(
        "UPDATE production_jobs SET status='needs_attention',stage='needs_attention',error_code='LEGACY_EXECUTION_UNKNOWN',error_message='Stopped during upgrade; provider result is unknown. Review before retrying.',completed_at=now() WHERE status='running'"
    )


def downgrade():
    op.drop_index("ix_audio_artifacts_job_id", table_name="audio_artifacts")
    op.drop_table("audio_artifacts")
    op.drop_table("maintenance_heartbeats")
    op.drop_table("job_retry_requests")
    op.drop_column("audio_assets", "delete_attempts")
    for column in ("execution_id", "retryable", "uncertain"):
        op.drop_column("provider_calls", column)
    op.drop_column("production_jobs", "current_execution_id")
    op.drop_table("job_executions")
    for column in ("provider_snapshot", "root_job_id", "retry_of_job_id"):
        op.drop_column("production_jobs", column)
