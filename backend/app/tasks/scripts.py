from app.services.script_production import (
    process_evaluation_job,
    process_generation_job,
)
from app.tasks.celery_app import celery_app
from app.tasks.runtime import run_task


@celery_app.task(name="app.worker.generate_script_task")
def generate_script_task(job_id):
    run_task(job_id, process_generation_job)


@celery_app.task(name="app.worker.evaluate_script_task")
def evaluate_script_task(job_id):
    run_task(job_id, process_evaluation_job)
