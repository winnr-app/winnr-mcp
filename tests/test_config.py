"""Tests for configuration loading."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from winnr_mcp.config import WinnrConfig, load_config


def test_config_from_env_var():
    """Config loads token from WINNR_API_TOKEN env var."""
    with patch.dict(os.environ, {"WINNR_API_TOKEN": "wnr_acc123_secretsecretsecretsecr"}):
        with patch("sys.argv", ["winnr-mcp"]):
            config = load_config()
    assert config.api_token == "wnr_acc123_secretsecretsecretsecr"
    assert config.api_url == "https://api.winnr.app"
    assert config.timeout == 30


def test_config_from_cli_args():
    """CLI args override env vars."""
    with patch.dict(os.environ, {"WINNR_API_TOKEN": "wnr_env_secretsecretsecretsecret"}):
        with patch("sys.argv", ["winnr-mcp", "--token", "wnr_cli_secretsecretsecretsecret", "--api-url", "https://custom.api.com", "--timeout", "60"]):
            config = load_config()
    assert config.api_token == "wnr_cli_secretsecretsecretsecret"
    assert config.api_url == "https://custom.api.com"
    assert config.timeout == 60


def test_config_missing_token_exits():
    """Missing token causes sys.exit."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove WINNR_API_TOKEN if it exists
        env = {k: v for k, v in os.environ.items() if k != "WINNR_API_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.argv", ["winnr-mcp"]):
                with pytest.raises(SystemExit):
                    load_config()


def test_config_invalid_token_format_exits():
    """Token not starting with wnr_ causes sys.exit."""
    with patch.dict(os.environ, {"WINNR_API_TOKEN": "sk_invalid_token"}):
        with patch("sys.argv", ["winnr-mcp"]):
            with pytest.raises(SystemExit):
                load_config()


def test_config_strips_trailing_slash():
    """API URL trailing slash is stripped."""
    with patch.dict(os.environ, {"WINNR_API_TOKEN": "wnr_test_secretsecretsecretsecret"}):
        with patch("sys.argv", ["winnr-mcp", "--api-url", "https://api.winnr.app/"]):
            config = load_config()
    assert config.api_url == "https://api.winnr.app"


def test_config_defaults():
    """WinnrConfig has correct defaults."""
    config = WinnrConfig(api_token="wnr_test_secretsecretsecretsecret")
    assert config.api_url == "https://api.winnr.app"
    assert config.timeout == 30
    assert config.permissions == ["read", "write"]
    assert config.account_id is None
    assert config.plan is None
