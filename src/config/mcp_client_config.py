"""Configuration helpers for connecting to the Jira MCP server.

This module is intentionally thin: it describes the MCP endpoint and the
well-known tool identifiers that the Agile Agents code expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from src.config.settings import settings


@dataclass(frozen=True)
class McpToolIds:
    """Canonical tool identifiers exposed by the MCP server.

    The concrete server implementation must expose tools with these IDs
    (or you should update this mapping accordingly).
    """

    # Jira
    jira_get_active_sprint_issues: str = "jira.get_active_sprint_issues"
    jira_comment_on_issue: str = "jira.comment_on_issue"
    jira_test_connection: str = "jira.test_connection"
    jira_get_issue_details: str = "jira.get_issue_details"
    jira_create_issue: str = "jira.create_issue"
    jira_seed_sample_backlog: str = "jira.seed_sample_backlog"

    # Scrum Master assistant orchestration (2-phase writes)
    scrum_master_plan_action: str = "scrum_master_assistant.plan_action"
    scrum_master_apply_action: str = "scrum_master_assistant.apply_action"
    scrum_master_handle_request: str = "scrum_master_assistant.handle_request"


MCP_TOOLS: Final[McpToolIds] = McpToolIds()


@dataclass
class McpConnectionConfig:
    """Connection parameters for the Jira MCP server."""

    endpoint: str
    env: str


def get_mcp_connection_config() -> McpConnectionConfig:
    """Build the MCP connection configuration from global settings."""

    if not settings.mcp_jira_github_endpoint:
        # Default to local FastAPI server root (no "/mcp" suffix).
        endpoint = "http://localhost:8000"
    else:
        endpoint = settings.mcp_jira_github_endpoint

    # Be tolerant if the environment variable includes a trailing
    # "/mcp" segment; strip it so that the HTTP client always calls
    # "{endpoint}/tools/{tool_id}", which matches the FastAPI routes.
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/mcp"):
        endpoint = endpoint[: -len("/mcp")]

    env = settings.mcp_env or settings.app_env

    return McpConnectionConfig(endpoint=endpoint, env=env)
