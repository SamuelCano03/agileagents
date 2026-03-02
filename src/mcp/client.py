"""MCP client stub used by Agile Agents.

This module will later wrap a concrete MCP Python client implementation.
For now it defines a thin interface that the rest of the code can depend on
without pulling in heavy networking details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import requests

from src.config.mcp_client_config import McpConnectionConfig, get_mcp_connection_config


@dataclass
class McpToolResult:
    """Represents the result of an MCP tool invocation."""

    tool_id: str
    arguments: Mapping[str, Any]
    raw_result: Any


class McpClient:
    """Very small facade over the underlying MCP connection.

    The real implementation will likely:
    - maintain a connection/session
    - perform JSON-RPC or similar calls
    - handle retries and error mapping
    For this competition project we keep the surface area intentionally small
    and focused on the Jira/GitHub tools we care about.
    """

    def __init__(self, config: McpConnectionConfig | None = None) -> None:
        self._config = config or get_mcp_connection_config()

    @property
    def config(self) -> McpConnectionConfig:
        return self._config

    def call_tool(self, tool_id: str, **kwargs: Any) -> McpToolResult:
        """Invoke a tool on the MCP server.

        This implementation calls a simple HTTP JSON endpoint exposed by
        the Jira MCP-style server. The convention is:

        POST {endpoint}/tools/{tool_id}
        body = kwargs as JSON

        The response is expected to be JSON and will be returned as-is
        in ``raw_result``.
        """

        base = self._config.endpoint.rstrip("/")
        url = f"{base}/tools/{tool_id}"

        resp = requests.post(url, json=kwargs, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # For convenience, if the server wraps the payload in a top-level
        # field like {"items": [...]}, callers can still access it from
        # raw_result.
        return McpToolResult(tool_id=tool_id, arguments=kwargs, raw_result=data.get("items") or data)
