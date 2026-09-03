"""Job tools — track async operations."""

from __future__ import annotations

import json
import time

from mcp.server.fastmcp import Context, FastMCP

from winnr_mcp import scopes as sc
from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import READ, clamp, is_str, seg, tool_error, winnr_tool

TERMINAL = ("completed", "error", "failed", "cancelled")
POLL_SECONDS = 3.0


def register_job_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register job tracking MCP tools."""

    @winnr_tool(mcp, sc.READ, READ, title="List jobs")
    def winnr_list_jobs(limit: int = 25, status: str | None = None, job_type: str | None = None) -> str:
        """List recent async jobs (domain setup, purchases, mailbox creation/deletion), newest first.

        Every write that provisions infrastructure returns a job_id; this shows the
        recent ones with their status. Also the place to look after a timeout on a
        purchase, to see whether the order actually went through before retrying.
        Not paginated: raise `limit` to see further back.

        Args:
            limit: Max jobs to return (1-100, default 25)
            status: Optional filter: queued, in_progress, completed, error
            job_type: Optional filter by job type (e.g. domain_setup, domain_purchase, user_create)
        """
        params: dict = {"limit": clamp(limit, 1, 100)}
        if is_str(status):
            params["filter[status]"] = status.strip().lower()
        if is_str(job_type):
            params["filter[type]"] = job_type.strip()
        return client.get("/v1/jobs", params=params).render()

    @winnr_tool(mcp, sc.READ, READ, title="Get job")
    def winnr_get_job(job_id: str) -> str:
        """Get the status and progress of one async job.

        Returns job type, status (queued / in_progress / completed / error), progress,
        result (for purchases: the same payload a synchronous purchase returns), error,
        and timestamps. For a blocking wait with progress updates use
        winnr_wait_for_job instead of polling this in a loop.

        Args:
            job_id: The job ID returned by the tool that started the work
        """
        if not is_str(job_id):
            return tool_error("job_id is required")
        return client.get(f"/v1/jobs/{seg(job_id)}").render()

    wait_doc = f"""Wait for an async job to finish, streaming progress while it runs.

Blocks up to `timeout_seconds` (1-{config.max_wait_seconds} on this server), checking every
few seconds and reporting progress notifications. Returns the final job when it reaches
completed/error, or the latest snapshot with "timed_out": true if it is still running —
call again to keep waiting. Domain provisioning takes a few minutes; mailbox creation
about a minute.

Args:
    job_id: The job ID to wait for
    timeout_seconds: How long to wait this call (1-{config.max_wait_seconds}, default 60)
"""

    @winnr_tool(mcp, sc.READ, READ, title="Wait for job", description=wait_doc)
    async def winnr_wait_for_job(job_id: str, ctx: Context, timeout_seconds: int = 60) -> str:
        if not is_str(job_id):
            return tool_error("job_id is required")
        budget = clamp(timeout_seconds, 1, config.max_wait_seconds)
        deadline = time.monotonic() + budget
        path = f"/v1/jobs/{seg(job_id)}"
        last: dict | None = None
        polls = 0
        while True:
            response = client.get(path)
            if not response.ok:
                return response.error_json()
            last = response.data if isinstance(response.data, dict) else {"raw": response.data}
            polls += 1
            status = str(last.get("status") or "").lower()
            progress = last.get("progress")
            pct = _progress_pct(progress, status)
            try:
                await ctx.report_progress(pct, 100, f"{job_id}: {status or 'unknown'}")
            except Exception:  # noqa: BLE001 — progress is best-effort
                pass
            if status in TERMINAL:
                return json.dumps({"data": last, "polls": polls}, indent=2, default=str)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return json.dumps(
                    {"data": last, "timed_out": True, "waited_seconds": budget, "polls": polls,
                     "note": "Still running. Call winnr_wait_for_job again to keep waiting."},
                    indent=2, default=str,
                )
            time.sleep(min(POLL_SECONDS, remaining))



def _progress_pct(progress: object, status: str) -> float:
    if status in ("completed",):
        return 100.0
    if isinstance(progress, dict):
        done, total = progress.get("completed") or progress.get("done"), progress.get("total")
        try:
            if total:
                return max(0.0, min(99.0, 100.0 * float(done or 0) / float(total)))
        except (TypeError, ValueError):
            pass
        if isinstance(progress.get("percent"), (int, float)):
            return max(0.0, min(99.0, float(progress["percent"])))
    if isinstance(progress, (int, float)):
        return max(0.0, min(99.0, float(progress)))
    return 50.0 if status == "in_progress" else 5.0
