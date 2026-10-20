from sqlalchemy import select

from app.models.articles import Article
from app.schemas.jobs import JobOut
from app.services.retries import capabilities


async def decorate(session, job):

    result = JobOut.model_validate(job)

    result.article_title = (
        await session.scalar(select(Article.title).where(Article.id == job.article_id))
        or ""
    )
    result.root_job_id = job.root_job_id or job.id
    for key, value in (await capabilities(session, job)).items():
        if key != "resume_artifact_id":
            setattr(result, key, value)
    return result
