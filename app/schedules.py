"""
AgentOS Schedules
==================
"""

from os import getenv
from typing import Any

from agno.scheduler import ScheduleManager
from agno.utils.log import log_info, log_warning

from db import get_postgres_db


def env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var, accepting 1/true/yes (any casing) as true."""
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _register(
    manager: ScheduleManager,
    *,
    name: str,
    cron: str,
    endpoint: str,
    payload: dict[str, Any],
    description: str,
    enabled_on_create: bool = True,
) -> None:
    """Create or update one schedule; a failure logs a warning instead of crashing startup.

    The enabled bit belongs to the user (AgentOS UI or the /schedules API) after first
    creation: the update path refreshes cron/endpoint/payload/description but never
    touches `enabled`, so a toggle flipped at runtime survives every reboot.
    `enabled_on_create` only sets the state a brand-new schedule starts in.
    """
    try:
        schedule = manager.create(
            name=name,
            cron=cron,
            endpoint=endpoint,
            payload=payload,
            description=description,
            if_exists="update",
        )
        # A fresh insert comes back with updated_at unset; every later write (the update
        # path, enable, disable) stamps it. This also self-heals a crash between create
        # and disable: the row keeps updated_at=None, so the next boot retries here.
        created = schedule.updated_at is None
        if created and not enabled_on_create:
            disabled = manager.disable(schedule.id)
            if disabled is None or disabled.enabled:
                raise RuntimeError("created enabled but could not be disabled — will retry next boot")
    except Exception as exc:
        log_warning(f"schedules: could not register '{name}': {exc}")
    else:
        if created and not enabled_on_create:
            log_info(f"schedules: registered '{name}' (disabled — enable it from the AgentOS UI)")
        else:
            log_info(f"schedules: registered '{name}'")


def register_schedules() -> None:
    """Register schedules (idempotent and fail-soft).

    The deployment check runs daily by default. Run-evals is always registered but starts
    disabled because it uses model calls — turn it on from the AgentOS UI when you want it.
    To add your own, add an `_register(...)` call below — pass `enabled_on_create=False`
    if it should ship as a visible, off-by-default toggle.
    """
    if getenv("ENABLE_SCHEDULED_EVALS") is not None:
        log_warning(
            "schedules: ENABLE_SCHEDULED_EVALS is no longer read — the run-evals schedule is "
            "always registered and its enabled state is managed from the AgentOS UI or the "
            "/schedules API."
        )

    try:
        manager = ScheduleManager(get_postgres_db())
    except Exception as exc:
        log_warning(f"schedules: could not initialize ScheduleManager: {exc}")
        return

    if env_flag("ENABLE_DEPLOY_CHECK", default=True):
        _register(
            manager,
            name="deployment-check",
            cron="0 13 * * *",  # 13:00 UTC daily
            endpoint="/workflows/deployment-check/runs",
            payload={"message": "Scheduled deployment check."},
            description="Daily: verify platform wiring and readiness.",
        )
    else:
        log_info("schedules: deployment-check disabled (ENABLE_DEPLOY_CHECK=False)")

    _register(
        manager,
        name="run-evals",
        cron="0 14 * * *",  # 14:00 UTC daily
        endpoint="/workflows/run-evals/runs",
        payload={"message": "Scheduled eval run."},
        description="Daily: run the eval suite and report regressions.",
        enabled_on_create=False,
    )
