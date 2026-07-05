"""
Run Evals
=========

python -m evals                         # run all cases (concise UI)
python -m evals --profile smoke         # run a tagged subset
python -m evals --case <name>           # run one case
python -m evals --json-output out.json  # write machine-readable results
python -m evals -v                      # stream the agent's run with full panels

Each case runs the agent once, then optionally checks the response with
`AgentAsJudgeEval` (when `criteria` is set) and
`ReliabilityEval` (when `expected_tool_calls` is set).

Both log to Postgres through `eval_db`. Connect your AgentOS at os.agno.com to see history.
"""

# Hydrate os.environ from .env before any module that reads env at import time
# (db_url, model factories, etc.). Pre-existing shell vars take precedence.
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from uuid import uuid4  # noqa: E402

import typer  # noqa: E402
from agno.eval import AgentAsJudgeEval, ReliabilityEval  # noqa: E402
from agno.run.agent import RunOutput  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.status import Status  # noqa: E402
from rich.table import Table  # noqa: E402

from evals.cases import CASES, Case, delete_new_components, eval_db, snapshot_component_ids  # noqa: E402

app = typer.Typer(add_completion=False, no_args_is_help=False, pretty_exceptions_show_locals=False)
console = Console()


def _agent_id(case: Case) -> str:
    return case.agent.id or case.name


@dataclass
class CaseOutcome:
    name: str
    agent_id: str
    profiles: tuple[str, ...]
    duration_seconds: float = 0.0
    judge_passed: bool | None = None
    reliability_passed: bool | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        checks = [c for c in (self.judge_passed, self.reliability_passed) if c is not None]
        return bool(checks) and all(checks)


async def _run_case_inner_async(case: Case, *, verbose: bool) -> CaseOutcome:
    if case.criteria is None and case.expected_tool_calls is None:
        return CaseOutcome(
            name=case.name,
            agent_id=_agent_id(case),
            profiles=case.profiles,
            error="case has no checks configured: set criteria and/or expected_tool_calls",
        )

    judge_passed: bool | None = None
    rel_passed: bool | None = None
    judge_err: str | None = None
    rel_err: str | None = None

    # Dedicated session_id per case so eval traffic doesn't bleed into agent
    # history, and so verbose mode can fetch the run back via aget_last_run_output.
    session_id = f"eval-{case.name}-{uuid4().hex[:8]}"

    response: RunOutput | None
    try:
        if verbose:
            # Stream the agent run with rich panels (Message → Tool Calls →
            # Response), same UI as os.agno.com. aprint_response returns None,
            # so fetch the RunOutput from storage afterward for the eval checks.
            await case.agent.aprint_response(
                input=case.input,
                stream=True,
                session_id=session_id,
                markdown=True,
            )
            response = await case.agent.aget_last_run_output(session_id=session_id)
        else:
            response = await _run_with_live_spinner(case, session_id)
        if response is None:
            return CaseOutcome(
                name=case.name,
                agent_id=_agent_id(case),
                profiles=case.profiles,
                error="agent: no run output recorded",
            )
    except Exception as exc:
        return CaseOutcome(
            name=case.name,
            agent_id=_agent_id(case),
            profiles=case.profiles,
            error=f"agent.arun: {type(exc).__name__}: {exc}",
        )

    output_str = str(response.content) if response.content else ""

    if not verbose:
        _print_response_concise(response, output_str)

    if case.criteria is not None:
        try:
            judge = await AgentAsJudgeEval(
                name=case.name,
                criteria=case.criteria,
                scoring_strategy="binary",
                db=eval_db,
            ).arun(input=case.input, output=output_str, print_results=verbose)
        except Exception as exc:
            judge_err = f"judge: {type(exc).__name__}: {exc}"
        else:
            if judge and judge.results:
                judge_passed = judge.results[0].passed
                if not verbose:
                    _print_judge_verdict(judge.results[0])
            else:
                judge_err = "judge: returned no result"

    if case.expected_tool_calls is not None:
        try:
            rel = ReliabilityEval(
                name=case.name,
                agent_response=response,
                expected_tool_calls=list(case.expected_tool_calls),
                allow_additional_tool_calls=case.allow_additional_tool_calls,
                db=eval_db,
            ).run(print_results=verbose)
        except Exception as exc:
            rel_err = f"reliability: {type(exc).__name__}: {exc}"
        else:
            if rel is None:
                rel_err = "reliability: returned no result"
            else:
                rel_passed = rel.eval_status == "PASSED"
                if not verbose:
                    _print_reliability_verdict(rel, case.expected_tool_calls)

    return CaseOutcome(
        name=case.name,
        agent_id=_agent_id(case),
        profiles=case.profiles,
        judge_passed=judge_passed,
        reliability_passed=rel_passed,
        error="; ".join(e for e in (judge_err, rel_err) if e) or None,
    )


async def _run_with_live_spinner(case: Case, session_id: str) -> RunOutput | None:
    """Stream the agent's run with a single-line spinner that updates per tool call.

    Avoids freezing the screen during long agent calls without spamming the user
    with the full streaming UI. Captures the final RunOutput via yield_run_output.
    """
    base_label = f"[bold]running[/bold] {case.agent.id}…"
    spinner = Status(base_label, spinner="dots")

    response: RunOutput | None = None
    with Live(spinner, console=console, transient=True, refresh_per_second=10):
        async for event in case.agent.arun(
            input=case.input,
            stream=True,
            stream_events=True,
            yield_run_output=True,
            session_id=session_id,
        ):
            if isinstance(event, RunOutput):
                response = event
                continue
            event_type = getattr(event, "event", None)
            if event_type == "ToolCallStarted":
                tool = getattr(event, "tool", None)
                tool_name = getattr(tool, "tool_name", None)
                if tool_name:
                    spinner.update(f"[bold]running[/bold] {case.agent.id} → [cyan]{tool_name}[/cyan]…")
            elif event_type == "ToolCallCompleted":
                spinner.update(base_label)

    return response


def _print_response_concise(response: RunOutput, output_str: str) -> None:
    """Plain-text response + one-line tool summary. Used in default (non-verbose) mode."""
    console.print()
    console.print("[bold]Response[/bold]")
    console.print(output_str or "[dim](empty)[/dim]")

    tools = response.tools or []
    if tools:
        names = ", ".join(t.tool_name or "?" for t in tools)
        console.print(f"\n[dim]tools fired:[/dim] {names}")


def _print_judge_verdict(eval_result: object) -> None:
    passed: bool = bool(getattr(eval_result, "passed", False))
    reason: str = str(getattr(eval_result, "reason", "") or "")
    style = "green" if passed else "red"
    tag = "PASS" if passed else "FAIL"
    console.print(f"\n[bold]Judge:[/bold] [{style}]{tag}[/{style}]")
    if reason:
        console.print(f"[dim]  {reason}[/dim]")


def _print_reliability_verdict(rel_result: object, expected_tools: tuple[str, ...]) -> None:
    passed = getattr(rel_result, "eval_status", "") == "PASSED"
    style = "green" if passed else "red"
    tag = "PASS" if passed else "FAIL"
    expected = ", ".join(expected_tools)
    console.print(f"\n[bold]Reliability:[/bold] [{style}]{tag}[/{style}]  [dim]expected: {expected}[/dim]")


async def _run_case_async(case: Case, *, verbose: bool, timeout_seconds: int) -> CaseOutcome:
    start = time.perf_counter()
    pre_run_ids: set[str] | None = None
    cleanup_error: str | None = None
    if case.cleanup_new_components:
        pre_run_ids = await asyncio.to_thread(snapshot_component_ids)
    try:
        outcome = await asyncio.wait_for(
            _run_case_inner_async(case, verbose=verbose),
            timeout=case.timeout_seconds or timeout_seconds,
        )
    except TimeoutError:
        outcome = CaseOutcome(
            name=case.name,
            agent_id=_agent_id(case),
            profiles=case.profiles,
            error=f"timeout: exceeded {case.timeout_seconds or timeout_seconds}s",
        )
        if pre_run_ids is not None:
            # Cancelling the run does not stop a sync Studio tool already executing in
            # its worker thread; give an in-flight create a moment to commit so the
            # teardown below sees it instead of leaking it.
            await asyncio.sleep(10)
    finally:
        # Teardown runs even on timeout — the mutation may have landed before the clock ran out.
        if pre_run_ids is not None:
            try:
                await asyncio.to_thread(delete_new_components, pre_run_ids)
            except Exception as exc:
                cleanup_error = f"cleanup: {type(exc).__name__}: {exc}"
                console.print(f"[yellow]cleanup failed for {case.name}: {exc}[/yellow]")
    if cleanup_error:
        # Surface leaked components in the outcome — the console warning is invisible
        # to the run-evals workflow, which runs the suite with the console quieted.
        outcome.error = "; ".join(error for error in (outcome.error, cleanup_error) if error)
    outcome.duration_seconds = round(time.perf_counter() - start, 3)
    return outcome


async def _run_cases_async(cases: list[Case], *, verbose: bool, timeout_seconds: int) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    for i, c in enumerate(cases, 1):
        console.rule(f"[bold]{c.name}[/bold]  [dim]{c.agent.id} · {i}/{len(cases)}[/dim]")
        outcomes.append(await _run_case_async(c, verbose=verbose, timeout_seconds=timeout_seconds))

    # Some toolkit transports schedule async close work after a case finishes.
    # Keeping the suite on one event loop and yielding once avoids noisy
    # "event loop is closed" cleanup warnings in normal eval output.
    await asyncio.sleep(0)
    return outcomes


def _case_matches(case: Case, *, case_name: str | None, profile: str | None) -> bool:
    if case_name and case.name != case_name:
        return False
    if profile and profile not in case.profiles:
        return False
    return True


def _build_payload(outcomes: list[CaseOutcome]) -> dict:
    passed = sum(1 for outcome in outcomes if outcome.passed)
    return {
        "summary": {
            "total": len(outcomes),
            "passed": passed,
            "failed": len(outcomes) - passed,
            "status": "PASS" if passed == len(outcomes) else "FAIL",
        },
        "cases": [
            {
                "name": outcome.name,
                "agent_id": outcome.agent_id,
                "profiles": list(outcome.profiles),
                "duration_seconds": outcome.duration_seconds,
                "judge_passed": outcome.judge_passed,
                "reliability_passed": outcome.reliability_passed,
                "passed": outcome.passed,
                "error": outcome.error,
            }
            for outcome in outcomes
        ],
    }


async def run_profile(profile: str | None = None, case_name: str | None = None, default_timeout: int = 120) -> dict:
    """Run the selected cases in-process and return the results payload.

    Embedding entrypoint (used by the run-evals workflow) — same selection and
    payload shape as the CLI, with the console UI suppressed.
    """
    cases = [c for c in CASES if _case_matches(c, case_name=case_name, profile=profile)]
    was_quiet = console.quiet
    console.quiet = True
    try:
        outcomes = await _run_cases_async(cases, verbose=False, timeout_seconds=default_timeout)
    finally:
        console.quiet = was_quiet
    return _build_payload(outcomes)


def _write_json_output(path: Path, outcomes: list[CaseOutcome]) -> None:
    payload = _build_payload(outcomes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _check_cell(passed: bool | None) -> str:
    if passed is None:
        return "[dim]—[/dim]"
    style = "green" if passed else "red"
    tag = "PASS" if passed else "FAIL"
    return f"[{style}]{tag}[/{style}]"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    case: str = typer.Option(None, "--case", help="Run only this case by name"),
    profile: str = typer.Option(None, "--profile", help="Run cases tagged with this profile: smoke, release, live"),
    timeout_seconds: int = typer.Option(120, "--timeout", help="Default per-case timeout in seconds"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write machine-readable JSON results"),
    list_cases: bool = typer.Option(False, "--list", help="List selected cases without running them"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Stream the full agent run with rich panels (Message → Tool Calls → Response), plus full eval tables.",
    ),
) -> None:
    """Run the eval suite, or one case with --case <name>."""
    if ctx.invoked_subcommand is not None:
        return

    cases = [c for c in CASES if _case_matches(c, case_name=case, profile=profile)]
    if not cases:
        selector = f"case={case!r}, profile={profile!r}"
        console.print(f"[red]no cases selected[/red] ({selector})")
        console.print(f"  [dim]available:[/dim] {', '.join(c.name for c in CASES)}")
        raise typer.Exit(2)

    if list_cases:
        table = Table(title="Eval Cases", title_style="bold sky_blue1", show_header=True, header_style="bold")
        table.add_column("Case", overflow="fold")
        table.add_column("Agent")
        table.add_column("Profiles")
        table.add_column("Timeout")
        for c in cases:
            table.add_row(c.name, c.agent.id, ", ".join(c.profiles), str(c.timeout_seconds or timeout_seconds))
        console.print(table)
        if json_output is not None:
            payload = {
                "cases": [
                    {
                        "name": c.name,
                        "agent_id": _agent_id(c),
                        "profiles": list(c.profiles),
                        "timeout_seconds": c.timeout_seconds or timeout_seconds,
                    }
                    for c in cases
                ]
            }
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            console.print(f"[dim]json output:[/dim] {json_output}")
        raise typer.Exit(0)

    outcomes = asyncio.run(_run_cases_async(cases, verbose=verbose, timeout_seconds=timeout_seconds))

    table = Table(title="Eval Summary", title_style="bold sky_blue1", show_header=True, header_style="bold")
    table.add_column("Case", overflow="fold")
    table.add_column("Judge")
    table.add_column("Reliability")
    table.add_column("Status")
    for o in outcomes:
        status = "[green]PASS[/green]" if o.passed else "[red]FAIL[/red]"
        table.add_row(o.name, _check_cell(o.judge_passed), _check_cell(o.reliability_passed), status)

    console.print()
    console.print(table)

    passed = sum(1 for o in outcomes if o.passed)
    failed = len(outcomes) - passed
    summary = f"[green]{passed}/{len(outcomes)} passed[/green]"
    if failed:
        summary += f", [red]{failed} failed[/red]"
    console.print(f"\n{summary}")

    if json_output is not None:
        _write_json_output(json_output, outcomes)
        console.print(f"[dim]json output:[/dim] {json_output}")

    for o in outcomes:
        if o.error:
            console.print(f"  [dim]{o.name}:[/dim] [red]{o.error}[/red]")

    raise typer.Exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    app()
