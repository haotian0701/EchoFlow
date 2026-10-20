from celery import Celery

from app.config import get_settings

celery_app = Celery(
    "echoflow", broker=get_settings().celery_broker_url, include=["app.worker"]
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={"app.worker.generate_script_task": {"queue": "script_generation"}},
)
celery_app.autodiscover_tasks(["app"])
