from app.services.audio_production import process_audio_job
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_task


@celery_app.task(name="app.audio_worker.generate_audio_task")
def generate_audio_task(job_id):
    run_task(job_id, process_audio_job)
