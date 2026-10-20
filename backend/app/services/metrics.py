from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.time import utc
from app.models.articles import Article
from app.models.audio import AudioAsset
from app.models.jobs import JobExecution, ProductionJob, ProviderCall
from app.models.reviews import Approval, Evaluation
from app.models.scripts import ScriptVersion


async def get_overview(session: AsyncSession, start=None, end=None, all_time=False):
    end = utc(end) if end else datetime.now(UTC)
    start = utc(start) if start else (None if all_time else end - timedelta(days=7))
    if start and start >= end:
        raise ApiError(
            422, "INVALID_TIME_RANGE", "The start must be before the end (UTC)"
        )

    def window(column):
        return [column < end] + ([column >= start] if start else [])

    jobs = (
        await session.scalars(
            select(ProductionJob).where(*window(ProductionJob.created_at))
        )
    ).all()
    calls = (
        await session.scalars(
            select(ProviderCall).where(*window(ProviderCall.created_at))
        )
    ).all()
    counts = Counter(job.status for job in jobs)
    job_ids = [job.id for job in jobs]
    executions = (
        (
            await session.scalars(
                select(JobExecution).where(JobExecution.job_id.in_(job_ids))
            )
        ).all()
        if job_ids
        else []
    )
    grouped = defaultdict(list)
    for execution in executions:
        grouped[execution.job_id].append(execution)
    durations = []
    for job in jobs:
        attempts = grouped[job.id]
        if (
            job.status in ("succeeded", "failed", "needs_attention")
            and attempts
            and all(
                e.status in ("succeeded", "failed") and e.completed_at for e in attempts
            )
        ):
            durations.append(
                sum(
                    max(0, (utc(e.completed_at) - utc(e.started_at)).total_seconds())
                    for e in attempts
                )
            )
    call_durations = {}
    for operation in ("script_generation", "script_evaluation", "audio_generation"):
        call_durations[operation] = {}
        for mode in ("mock", "real"):
            samples = [
                c.latency_ms
                for c in calls
                if c.operation == operation
                and c.latency_ms is not None
                and (c.provider == "mock") == (mode == "mock")
            ]
            call_durations[operation][mode] = {
                "average_ms": round(mean(samples), 2) if samples else None,
                "sample_count": len(samples),
            }
    # Current review queue is a point-in-time gauge, independent of the selected period.
    versions = (
        await session.execute(
            select(
                ScriptVersion.id, ScriptVersion.article_id, ScriptVersion.version
            ).order_by(ScriptVersion.version.desc())
        )
    ).all()
    seen, pending_review = set(), 0
    for script_id, article_id, version in versions:
        if article_id in seen:
            continue
        seen.add(article_id)
        evaluation = await session.scalar(
            select(Evaluation)
            .where(Evaluation.script_version_id == script_id)
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        )
        if (
            evaluation
            and evaluation.status == "succeeded"
            and not await session.scalar(
                select(Approval.id).where(Approval.evaluation_id == evaluation.id)
            )
        ):
            pending_review += 1
    parents = set(
        (
            await session.scalars(
                select(ProductionJob.retry_of_job_id).where(
                    ProductionJob.retry_of_job_id.is_not(None)
                )
            )
        ).all()
    )
    audio = (
        await session.execute(
            select(AudioAsset, Article.title)
            .join(Article, Article.id == AudioAsset.article_id)
            .where(AudioAsset.status == "available", *window(AudioAsset.created_at))
            .order_by(AudioAsset.created_at.desc())
            .limit(5)
        )
    ).all()
    failures = sorted(
        [j for j in jobs if j.status in ("failed", "needs_attention")],
        key=lambda j: utc(j.created_at),
        reverse=True,
    )[:5]
    unknown = sum(
        c.provider != "mock"
        and (
            (c.provider == "openrouter" and c.cost is None)
            or (c.provider == "elevenlabs" and c.provider_credits is None)
            or c.provider not in ("openrouter", "elevenlabs")
        )
        for c in calls
    )
    return {
        "time_range": {"from": start, "to": end},
        "total_jobs": len(jobs),
        "status_counts": {
            status: counts[status]
            for status in (
                "queued",
                "running",
                "retry_scheduled",
                "succeeded",
                "failed",
                "needs_attention",
            )
        },
        "success_rate": counts["succeeded"] / len(jobs) if jobs else None,
        "manual_retry_count": sum(j.retry_of_job_id is not None for j in jobs),
        "pending_review_count": pending_review,
        "unresolved_needs_attention_count": sum(
            j.status == "needs_attention" and j.id not in parents for j in jobs
        ),
        "active_processing": {
            "average_seconds": round(mean(durations), 3) if durations else None,
            "sample_count": len(durations),
            "terminal_job_count": sum(
                j.status in ("succeeded", "failed", "needs_attention") for j in jobs
            ),
        },
        "provider_latency": call_durations,
        "usage": {
            "openrouter_usd": float(
                sum(
                    c.cost
                    for c in calls
                    if c.provider == "openrouter" and c.cost is not None
                )
            ),
            "elevenlabs_credits": float(
                sum(
                    c.provider_credits
                    for c in calls
                    if c.provider == "elevenlabs" and c.provider_credits is not None
                )
            ),
            "unknown_cost_calls": unknown,
            "mock_calls": sum(c.provider == "mock" for c in calls),
            "total_calls": len(calls),
        },
        "recent_audio": [
            {
                "id": a.id,
                "article_title": title,
                "script_version_id": a.script_version_id,
                "created_at": a.created_at,
            }
            for a, title in audio
        ],
        "recent_failures": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "error_code": j.error_code,
                "error_message": j.error_message,
            }
            for j in failures
        ],
    }
