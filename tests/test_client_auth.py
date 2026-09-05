"""The bearer credential `client.py` uses when a password is set."""

from __future__ import annotations

import os

from kraft import auth


def test_ensure_mcp_token_creates_a_private_file_once(tmp_path):
    first = auth.ensure_mcp_token(tmp_path)
    assert first
    assert auth.read_mcp_token(tmp_path) == first
    # regenerating on every serve would invalidate a registered MCP client
    assert auth.ensure_mcp_token(tmp_path) == first


def test_the_token_file_is_not_world_readable(tmp_path):
    auth.ensure_mcp_token(tmp_path)
    assert os.stat(tmp_path / "mcp-token").st_mode & 0o777 == 0o600


def test_read_returns_none_when_there_is_no_token(tmp_path):
    assert auth.read_mcp_token(tmp_path) is None


def test_an_empty_token_file_is_replaced_not_trusted(tmp_path):
    (tmp_path / "mcp-token").write_text("   \n")
    assert auth.read_mcp_token(tmp_path) is None
    assert auth.ensure_mcp_token(tmp_path).strip()
