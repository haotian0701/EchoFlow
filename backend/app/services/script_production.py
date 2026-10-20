import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.provider_errors import ProviderError
from app.core.script_rules import PROMPT_VERSION, estimated_duration_seconds
from app.db.session import SessionLocal
from app.evaluation.rules import run_rules
from app.integrations.llm.evaluation import get_evaluation_provider
from app.integrations.llm.generation import get_provider
from app.integrations.provider_config import execution_settings
from app.models.articles import Article
from app.models.jobs import ProductionJob, ProviderCall
from app.models.reviews import Evaluation
from app.models.scripts import ScriptVersion
from app.schemas.scripts import ScriptGenerationConfig
from app.services.execution import (
    apply_call_metadata,
    event,
    execution_scope,
    fenced,
    finish,
    record_failure,
)


async def _process(job_id, kind, factory, provider):
    async with execution_scope(factory, job_id, kind) as execution_id:
        if execution_id is None:
            return
        evaluation_mode = kind == "script_evaluation"
        async with factory() as session:
            job = await session.get(ProductionJob, job_id)
            article = await session.get(Article, job.article_id)
            script = (
                await session.get(ScriptVersion, job.source_script_version_id)
                if evaluation_mode
                else None
            )
        rules = run_rules(article, script) if evaluation_mode else []
        try:
            settings = execution_settings(job.provider_snapshot)
            active = provider or (
                get_evaluation_provider(settings)
                if evaluation_mode
                else get_provider(settings)
            )
        except ProviderError as error:
            await record_failure(
                factory, job_id, execution_id, error, rule_issues=rules
            )
            return
        call_id = uuid.uuid4()
        async with factory() as session:
            job = await fenced(session, job_id, execution_id)
            if not job:
                return
            job.stage = "provider_call"
            session.add(
                ProviderCall(
                    id=call_id,
                    job_id=job_id,
                    execution_id=execution_id,
                    operation=kind,
                    provider=active.name,
                    requested_model=active.requested_model,
                    status="running",
                )
            )
            event(
                session,
                job,
                "provider_call",
                "Evaluating script quality"
                if evaluation_mode
                else "Generating the audio script",
            )
            await session.commit()
        started = time.monotonic()
        try:
            if evaluation_mode:
                result = await active.evaluate_script(article, script, rules, job_id)
            else:
                result = await active.generate_script(
                    article, ScriptGenerationConfig.model_validate(job.config), job_id
                )
        except ProviderError as error:
            error.metadata.setdefault(
                "latency_ms", round((time.monotonic() - started) * 1000)
            )
            await record_failure(factory, job_id, execution_id, error, call_id, rules)
            return
        # Accounting survives a later failure to save the business result.
        # A delayed response may update its own audit row, never the recovered job.
        async with factory() as session:
            call = await session.get(ProviderCall, call_id)
            if call and call.execution_id == execution_id:
                apply_call_metadata(call, vars(result))
                call.status, call.completed_at, call.uncertain = (
                    "succeeded",
                    datetime.now(UTC),
                    False,
                )
                await session.commit()
        async with factory() as session:
            job = await fenced(session, job_id, execution_id)
            if not job:
                return
            job.stage = "saving"
            call = await session.get(ProviderCall, call_id)
            apply_call_metadata(call, vars(result))
            call.status, call.completed_at = "succeeded", datetime.now(UTC)
            if evaluation_mode:
                evaluation = await session.get(Evaluation, job.result_evaluation_id)
                for key in (
                    "factual_consistency",
                    "content_coverage",
                    "audio_readiness",
                    "provider",
                    "requested_model",
                    "actual_model",
                ):
                    setattr(evaluation, key, getattr(result, key))
                evaluation.issues = [*rules, *result.issues]
                evaluation.status, evaluation.completed_at = (
                    "succeeded",
                    datetime.now(UTC),
                )
                message = "The evaluation report is ready"
            else:
                latest = await session.scalar(
                    select(func.max(ScriptVersion.version)).where(
                        ScriptVersion.article_id == job.article_id
                    )
                )
                script = ScriptVersion(
                    article_id=job.article_id,
                    version=(latest or 0) + 1,
                    text=result.text,
                    source="generated",
                    parent_version_id=job.source_script_version_id,
                    target_language=job.config["target_language"],
                    generation_config=job.config,
                    provider=result.provider,
                    requested_model=result.requested_model,
                    actual_model=result.actual_model,
                    prompt_version=PROMPT_VERSION,
                    estimated_duration_seconds=estimated_duration_seconds(
                        result.text, job.config["target_language"]
                    ),
                )
                session.add(script)
                await session.flush()
                job.result_script_version_id = script.id
                message = f"Script version {script.version} is ready"
            await finish(session, job)
            event(session, job, "succeeded", message)
            await session.commit()


async def process_generation_job(
    job_id, *, session_factory=SessionLocal, provider=None
):
    await _process(job_id, "script_generation", session_factory, provider)


async def process_evaluation_job(
    job_id, *, session_factory=SessionLocal, provider=None
):
    await _process(job_id, "script_evaluation", session_factory, provider)
