"""MCP server (stdio) that lets an agent use WorksFr through its HTTP API.

The sandbox stays in the backend. This process never runs tutorial code.
Run it with `python -m app.mcp_server`.
"""

import asyncio
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.config import get_settings

POLL_SECONDS = 2
GRACE_SECONDS = 60
MAX_OUTPUT_CHARS = 2000

_sleep = asyncio.sleep
_transport: httpx.AsyncBaseTransport | None = None

LABEL_HELP = (
    "Step labels: works = ran with no error; deprecated = ran but the library warns the API "
    "is going away; broken = failed in the sandbox; cant_verify = needs a real API key, GPU, "
    "large download or keyboard input, so it was not judged. Verdicts for broken steps: "
    "fixed = the replacement code ran in the sandbox with no error; fixed_unverified = it got "
    "past the broken API but stopped later only because the sandbox lacks a key, network or "
    "data; failed = no working fix found. A fix counts only because it ran in the sandbox, "
    "never because a model said so. output = what the step printed in the sandbox (stdout, "
    "then stderr), from the replacement when there is one, cut at 2,000 characters. "
    "There is no score or grade. "
    "Ran in the sandbox without an error. Its output wasn't compared with the tutorial's, "
    "so read it before you run it."
)

mcp = MCPServer(
    "worksfr",
    instructions="Checks whether a Python tutorial still works with today's library versions. "
    + LABEL_HELP,
)


class WorksFrUnavailable(ToolError):
    """A backend problem, shown to the agent as a tool error."""


def _base_url() -> str:
    return get_settings().worksfr_api_url.rstrip("/")


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else response.text[:200]


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    base = _base_url()
    try:
        async with httpx.AsyncClient(base_url=base, transport=_transport, timeout=30) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.TransportError:
        raise WorksFrUnavailable(
            f"WorksFr's backend isn't reachable at {base}. Start it with: uvicorn app.main:app"
        ) from None
    if response.is_error:
        raise WorksFrUnavailable(
            f"WorksFr's backend returned HTTP {response.status_code}: {_error_detail(response)}"
        )
    return response.json()


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for step in report.get("steps", []):
        row: dict[str, Any] = {
            "step": step["idx"] + 1,
            "label": step.get("label"),
            "reason": step.get("reason", ""),
            "verdict": step.get("verdict"),
        }
        output = step.get("output")
        if output:
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
            row["output"] = output
        if step.get("verdict") in ("fixed", "fixed_unverified"):
            row["replacement"] = step["final_code"]
            if step.get("explanation"):
                row["explanation"] = step["explanation"]
        steps.append(row)
    summary: dict[str, Any] = {
        "check_id": report["id"],
        "status": report["status"],
        "title": report.get("title"),
        "source": report.get("source"),
        "library_versions": report.get("versions", {}),
        "failed_packages": report.get("failed_packages", []),
        "steps": steps,
    }
    if report.get("error"):
        summary["error"] = report["error"]
    return summary


async def _progress(ctx: Context | None, step: float, message: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(step, None, message)
    except Exception:
        pass


async def _wait_for_report(check_id: str, ctx: Context | None) -> dict[str, Any]:
    limit = get_settings().check_timeout_seconds + GRACE_SECONDS
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    polls = 0
    while True:
        report = await _request("GET", f"/api/checks/{check_id}")
        if report["status"] in ("done", "failed"):
            return _summarize(report)
        if loop.time() >= deadline:
            raise WorksFrUnavailable(
                f"Check {check_id} was still running after {limit} seconds. "
                "Call get_report later to read the result."
            )
        polls += 1
        await _progress(ctx, polls, f"Check {check_id} is running")
        await _sleep(POLL_SECONDS)


_TUTORIAL_DOC = (
    "Check whether the Python tutorial at `url` still works with today's library versions. "
    "Runs every code step in a Docker sandbox and tries to repair the broken ones. "
    "Takes minutes. " + LABEL_HELP
)
_CODE_DOC = "Check pasted Python tutorial code the same way as check_tutorial. " + LABEL_HELP
_REPORT_DOC = (
    "Return the summary of an existing check: versions, failed packages and every step. "
    + LABEL_HELP
)


@mcp.tool(description=_TUTORIAL_DOC)
async def check_tutorial(url: str, ctx: Context | None = None) -> dict[str, Any]:
    created = await _request("POST", "/api/checks", json={"url": url})
    return await _wait_for_report(created["check_id"], ctx)


@mcp.tool(description=_CODE_DOC)
async def check_code(
    code: str, title: str | None = None, ctx: Context | None = None
) -> dict[str, Any]:
    created = await _request("POST", "/api/checks/code", json={"code": code, "title": title})
    return await _wait_for_report(created["check_id"], ctx)


@mcp.tool(description=_REPORT_DOC)
async def get_report(check_id: str) -> dict[str, Any]:
    return _summarize(await _request("GET", f"/api/checks/{check_id}"))


@mcp.tool()
async def recent_checks(limit: int = 10) -> list[dict[str, Any]]:
    """List the most recent checks: id, title, status and created_at."""
    reports = await _request("GET", "/api/checks")
    return [
        {
            "id": r["id"],
            "title": r.get("title"),
            "status": r["status"],
            "created_at": r.get("created_at", ""),
        }
        for r in reports[: max(limit, 0)]
    ]


if __name__ == "__main__":
    mcp.run()
