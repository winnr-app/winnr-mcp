"""Job tools — track async operations."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import READ, clamp


def register_job_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register job tracking MCP tools."""

    @mcp.tool(annotations=READ)
    def winnr_list_jobs(limit: int = 25, cursor: str | None = None) -> str:
        """List recent async jobs (domain setup, purchases, mailbox creation/deletion).

        Every write that provisions infrastructure returns a job_id; this shows the
        recent ones with their status. Also the place to look after a timeout on a
        purchase, to see whether the order actually went through before retrying.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
        """
        params: dict = {"limit": clamp(limit, 1, 100)}
        if cursor:
            params["cursor"] = cursor
        return client.get("/v1/jobs", params=params).render()

    @mcp.tool(annotations=READ)
    def winnr_get_job(job_id: str) -> str:
        """Get the status and progress of one async job.

        Returns job type, status (queued / in_progress / completed / error), progress,
        result (for purchases: the same payload a synchronous purchase returns), error,
        and timestamps. Poll every 10-20 seconds; domain provisioning takes a few
        minutes, mailbox creation about a minute.

        Args:
            job_id: The job ID returned by the tool that started the work
        """
        if not job_id or not job_id.strip():
            from winnr_mcp.tools._common import tool_error

            return tool_error("job_id is required")
        return client.get(f"/v1/jobs/{job_id.strip()}").render()
