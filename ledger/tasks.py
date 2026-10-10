from celery import shared_task

from . import services


@shared_task(name="ledger.tasks.expire_wait")
def expire_wait_task(wait_id: str) -> bool:
    return services.expire_wait(wait_id)


@shared_task(name="ledger.tasks.sweep_expired_waits")
def sweep_expired_waits() -> int:
    return services.sweep_expired()
