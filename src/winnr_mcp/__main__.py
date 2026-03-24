"""CLI entrypoint for winnr-mcp."""

from __future__ import annotations

from winnr_mcp.config import load_config
from winnr_mcp.server import create_server


def main() -> None:
    """Run the Winnr MCP server."""
    config = load_config()
    server = create_server(config)
    server.run()


if __name__ == "__main__":
    main()
