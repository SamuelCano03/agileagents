"""Jira-oriented helper functions built on top of the MCP client.

These helpers keep Jira-specific logic separate from the orchestration
and agent code. They are intentionally small wrappers so that agents
can work at the level of domain concepts instead of raw tool IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.mcp.client import McpClient, McpToolResult
from src.config.mcp_client_config import MCP_TOOLS


@dataclass
class JiraIssue:
    """Representation of a Jira issue relevant for stand-ups.

    We intentionally include a few more fields than before so that
    agents can speak more concretely about work: story points,
    sprint name/state and priority are all useful for daily updates.
    """

    key: str
    summary: str
    status: str
    assignee: str | None
    story_points: float | None = None
    sprint_name: str | None = None
    sprint_state: str | None = None
    priority: str | None = None


def get_active_sprint_issues(client: McpClient, *, assignee: str | None = None) -> Iterable[JiraIssue]:
    """Fetch issues from the active sprint, optionally filtered by assignee.

    The exact filtering logic lives on the MCP server; here we simply
    forward arguments and adapt the response into JiraIssue objects.
    """

    result: McpToolResult = client.call_tool(
        MCP_TOOLS.jira_get_active_sprint_issues,
        assignee=assignee,
    )

    raw_items: list[dict[str, Any]] = list(result.raw_result or [])
    for item in raw_items:
        yield JiraIssue(
            key=item.get("key", ""),
            summary=item.get("summary", ""),
            status=item.get("status", "Unknown"),
            assignee=item.get("assignee"),
            story_points=item.get("story_points"),
            sprint_name=item.get("sprint_name"),
            sprint_state=item.get("sprint_state"),
            priority=item.get("priority"),
        )


def comment_on_issue(client: McpClient, *, key: str, comment: str) -> McpToolResult:
    """Create a comment on a Jira issue via MCP."""

    return client.call_tool(
        MCP_TOOLS.jira_comment_on_issue,
        key=key,
        comment=comment,
    )


def test_connection(client: McpClient) -> McpToolResult:
    """Test Jira connectivity and return basic identity/project information."""

    return client.call_tool(MCP_TOOLS.jira_test_connection)


def get_issue_details(
    client: McpClient,
    *,
    key: str,
    fields: list[str] | None = None,
) -> McpToolResult:
    """Fetch detailed Jira issue data via MCP."""

    return client.call_tool(
        MCP_TOOLS.jira_get_issue_details,
        key=key,
        fields=fields or ["summary", "description", "status", "assignee", "priority", "updated"],
    )


def create_issue(
    client: McpClient,
    *,
    summary: str,
    description: str | None = None,
    issue_type: str = "Task",
    story_points: float | None = None,
) -> McpToolResult:
    """Create a Jira issue via MCP.

    Note: the MCP server can enforce write guards through environment
    variables to avoid accidental writes.
    """

    return client.call_tool(
        MCP_TOOLS.jira_create_issue,
        summary=summary,
        description=description,
        issue_type=issue_type,
        story_points=story_points,
    )


def seed_sample_backlog(
    client: McpClient,
    *,
    topic: str = "Agile training",
    count: int = 5,
) -> McpToolResult:
    """Create a sample backlog in Jira for demos/training via MCP."""

    return client.call_tool(
        MCP_TOOLS.jira_seed_sample_backlog,
        topic=topic,
        count=count,
    )


def plan_scrum_master_action(
    client: McpClient,
    *,
    action: str,
    params: dict[str, Any],
    reason: str | None = None,
) -> McpToolResult:
    """Phase 1: plan a Jira write action without executing it."""

    return client.call_tool(
        MCP_TOOLS.scrum_master_plan_action,
        action=action,
        params=params,
        reason=reason,
    )


def apply_scrum_master_action(
    client: McpClient,
    *,
    plan_id: str,
    confirm: bool,
    confirmation_text: str = "CONFIRM",
) -> McpToolResult:
    """Phase 2: execute a previously planned Jira write action."""

    return client.call_tool(
        MCP_TOOLS.scrum_master_apply_action,
        plan_id=plan_id,
        confirm=confirm,
        confirmation_text=confirmation_text,
    )


def handle_scrum_master_request(
    client: McpClient,
    *,
    request_text: str,
    reason: str | None = None,
) -> McpToolResult:
    """Parse a natural language request into a planned Jira assistant action."""

    return client.call_tool(
        MCP_TOOLS.scrum_master_handle_request,
        request_text=request_text,
        reason=reason,
    )
