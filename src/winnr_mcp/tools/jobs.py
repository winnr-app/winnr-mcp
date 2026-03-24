"""Job tools — track async operations."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_job_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register job tracking MCP tools."""

    @mcp.tool()
    def winnr_list_jobs(limit: int = 25, cursor: str | None = None) -> str:
        """List recent async jobs (domain setup, user creation, etc.).

        Jobs are created when you perform operations like domain setup,
        email user creation, or bulk operations. Use this to see recent
        job activity and their statuses.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
        """
        params: dict = {"limit": min(max(limit, 1), 100)}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/jobs", params=params)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_job(job_id: str) -> str:
        """Get the status and progress of a specific async job.

        Returns job type, status (queued/in_progress/completed/error),
        progress details, result, and timestamps.

        Args:
            job_id: The job ID (returned when creating domains, users, etc.)
        """
        response = client.get(f"/v1/jobs/{job_id}")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()
