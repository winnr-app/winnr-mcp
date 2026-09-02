"""Winnr MCP Server — AI-powered email infrastructure management."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("winnr-mcp")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0-dev"
