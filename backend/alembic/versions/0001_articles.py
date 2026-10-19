"""Create articles and import attempts.

Revision ID: 0001_articles
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_articles"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("import_type", sa.String(10), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("language IN ('en', 'de')", name="ck_articles_language"),
        sa.CheckConstraint(
            "import_type IN ('manual', 'url')", name="ck_articles_import_type"
        ),
    )
    op.create_index(
        "ix_articles_content_hash", "articles", ["content_hash"], unique=True
    )
    op.create_index("ix_articles_created_at", "articles", ["created_at"])
    op.create_index("ix_articles_title", "articles", ["title"])
    op.create_table(
        "import_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("article_id", sa.Uuid(), sa.ForeignKey("articles.id"), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'duplicate', 'failed')",
            name="ck_import_attempts_status",
        ),
    )
    op.create_index("ix_import_attempts_created_at", "import_attempts", ["created_at"])


def downgrade() -> None:
    op.drop_table("import_attempts")
    op.drop_table("articles")
