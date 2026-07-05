"""
Run Evals
=========

Workflow that runs an eval profile and returns a compact report.
"""

import asyncio
from os import getenv

from agno.workflow.step import Step, StepInput, StepOutput
from agno.workflow.workflow import Workflow

from db import get_postgres_db


def _int_env(name: str, default: int) -> int:
    value = getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _format_summary(payload: dict) -> str:
    summary = payload.get("summary", {})
    status = summary.get("status", "FAIL")
    lines = [
        "# Evals",
        "",
        f"Overall: **{status}** ({summary.get('passed', 0)}/{summary.get('total', 0)} passed)",
        "",
    ]
    for case in payload.get("cases", []):
        result = "PASS" if case.get("passed") else "FAIL"
        duration = case.get("duration_seconds", 0)
        detail = f"{result} `{case.get('name')}` ({duration}s)"
        if case.get("error"):
            detail += f" — {case['error']}"
        lines.append(f"- {detail}")
    return "\n".join(lines)


async def run_evals_step(_step_input: StepInput) -> StepOutput:
    """Run the configured eval profile in-process and return a markdown summary."""
    # Imported lazily so the eval suite only loads when the workflow actually runs.
    from evals.__main__ import run_profile

    profile = getenv("EVALS_PROFILE", "smoke")
    case_timeout_seconds = _int_env("EVALS_CASE_TIMEOUT_SECONDS", 90)
    # 900 bounds the smoke profile's worst case: per-case ceilings sum past 600s
    # now that builder cases add snapshot/teardown DB calls and a timeout grace.
    suite_timeout_seconds = _int_env("EVALS_SUITE_TIMEOUT_SECONDS", 900)

    try:
        payload = await asyncio.wait_for(
            run_profile(profile=profile, default_timeout=case_timeout_seconds),
            timeout=suite_timeout_seconds,
        )
    except TimeoutError:
        return StepOutput(
            content=(
                "# Evals\n\n"
                f"Overall: **FAIL** — `{profile}` profile exceeded {suite_timeout_seconds}s "
                "(EVALS_SUITE_TIMEOUT_SECONDS)."
            ),
            success=False,
        )

    return StepOutput(
        content=_format_summary(payload),
        success=payload.get("summary", {}).get("status") == "PASS",
    )


run_evals = Workflow(
    id="run-evals",
    name="Run Evals",
    description="Run an eval profile and report pass/fail status.",
    db=get_postgres_db(),
    steps=[Step(name="run-evals", executor=run_evals_step)],
)
