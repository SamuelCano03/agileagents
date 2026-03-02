"""Helpers to build context for Scrum ceremonies.

Right now this uses simple placeholders; later it will call MCP tools
for Jira to build a richer view per team member.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.conversations.messages import UserContext
from src.mcp.client import McpClient
from src.mcp.tools_jira import get_active_sprint_issues, JiraIssue


@dataclass
class MemberContext:
    """Aggregated context for a single team member."""

    name: str
    jira_issues: list[JiraIssue]
    commits: list[str]
    used_team_fallback: bool = False


@dataclass
class StandupContext:
    """Context object passed into the daily stand-up orchestration."""

    user: UserContext
    members: list[MemberContext]


def build_standup_context(
    *,
    user: UserContext,
    member_names: Iterable[str],
    mcp_client: McpClient,
    fallback_jira_items: Iterable[JiraIssue] | None = None,
) -> StandupContext:
    """Create a basic stand-up context.

    This function intentionally keeps its behavior minimal so that the
    CLI can run even before the MCP client is fully wired. Once MCP
    calls are available, this will provide real data per member.
    """

    members: list[MemberContext] = []

    team_jira_items: list[JiraIssue] = []
    try:
        team_jira_items = list(get_active_sprint_issues(mcp_client, assignee=None))
    except Exception:
        team_jira_items = []

    if not team_jira_items and fallback_jira_items is not None:
        team_jira_items = list(fallback_jira_items)

    for index, name in enumerate(member_names):
        # The initial implementation uses placeholder lists; the calls
        # will raise until the MCP client is implemented. This keeps
        # the orchestration logic independent of transport details.
        jira_items: list[JiraIssue] = []
        commits: list[str] = []

        try:
            jira_items = list(get_active_sprint_issues(mcp_client, assignee=name))
        except Exception:
            jira_items = []

        used_team_fallback = False
        if not jira_items and team_jira_items:
            # Fallback for demo environments where assignee display names in Jira
            # do not exactly match local member names.
            start = (index * 2) % max(len(team_jira_items), 1)
            jira_items = team_jira_items[start : start + 2]
            if not jira_items:
                jira_items = team_jira_items[:2]
            used_team_fallback = bool(jira_items)

        members.append(
            MemberContext(
                name=name,
                jira_issues=jira_items,
                commits=commits,
                used_team_fallback=used_team_fallback,
            )
        )

    return StandupContext(user=user, members=members)
