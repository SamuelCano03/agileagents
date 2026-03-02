"""Role definitions for Scrum agents."""

from __future__ import annotations

from enum import Enum


class AgentRole(str, Enum):
    SCRUM_MASTER = "scrum_master"
    PRODUCT_OWNER = "product_owner"
    TEAM_MEMBER = "team_member"
