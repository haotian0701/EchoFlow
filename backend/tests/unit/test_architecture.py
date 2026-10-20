"""Protect process boundaries and serialized message compatibility during refactors."""

import ast
from pathlib import Path

from app.models import Base
from app.tasks.audio import generate_audio_task
from app.tasks.scripts import evaluate_script_task, generate_script_task

APP = Path(__file__).resolve().parents[2] / "app"


def test_service_and_integration_layers_do_not_import_process_entrypoints():
    forbidden = ("app.api", "app.tasks", "fastapi", "celery")
    for folder in ("services", "integrations", "models", "evaluation", "core"):
        for path in (APP / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(forbidden), (path, node.module)
                elif isinstance(node, ast.Import):
                    assert not any(
                        alias.name.startswith(forbidden) for alias in node.names
                    ), path


def test_existing_celery_message_names_remain_registered():
    assert generate_script_task.name == "app.worker.generate_script_task"
    assert evaluate_script_task.name == "app.worker.evaluate_script_task"
    assert generate_audio_task.name == "app.audio_worker.generate_audio_task"


def test_all_tables_are_registered_for_alembic_after_model_split():
    assert set(Base.metadata.tables) == {
        "articles",
        "import_attempts",
        "script_versions",
        "production_jobs",
        "task_outbox",
        "job_events",
        "provider_calls",
        "evaluations",
        "approvals",
        "audio_assets",
        "audio_requests",
        "job_executions",
        "job_retry_requests",
        "maintenance_heartbeats",
        "audio_artifacts",
    }
