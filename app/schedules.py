"""
AgentOS Schedules
==================
"""

from os import getenv

from agno.scheduler import ScheduleManager
from agno.utils.log import log_info, log_warning

from db import get_postgres_db


def register_schedules() -> None:
    """Register schedules (idempotent and fail-soft).

    The deployment check runs daily by default. Scheduled evals are opt-in because they use model calls.
    """
    try:
        manager = ScheduleManager(get_postgres_db())
    except Exception as exc:
        log_warning(f"schedules: could not initialize ScheduleManager: {exc}")
        return

    if getenv("ENABLE_DEPLOY_CHECK", "True") == "True":
        try:
            manager.create(
                name="deployment-check",
                cron="0 13 * * *",  # 13:00 UTC daily
                endpoint="/workflows/deployment-check/runs",
                payload={"message": "Scheduled deployment check."},
                description="Daily: verify platform wiring and readiness.",
                if_exists="update",
            )
        except Exception as exc:
            log_warning(f"schedules: could not register 'deployment-check': {exc}")
        else:
            log_info("schedules: registered 'deployment-check'")
    else:
        log_info("schedules: deployment-check disabled (ENABLE_DEPLOY_CHECK=False)")

    if getenv("ENABLE_SCHEDULED_EVALS", "False") == "True":
        try:
            manager.create(
                name="run-evals",
                cron="0 14 * * *",  # 14:00 UTC daily
                endpoint="/workflows/run-evals/runs",
                payload={"message": "Scheduled eval run."},
                description="Daily: run the eval suite and report regressions.",
                if_exists="update",
            )
        except Exception as exc:
            log_warning(f"schedules: could not register 'run-evals': {exc}")
        else:
            log_info("schedules: registered 'run-evals'")
    else:
        log_info("schedules: run-evals disabled (ENABLE_SCHEDULED_EVALS=True to enable)")
