"""Shared message models used by the orchestration layer and agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class ConversationMessage:
    """Represents a single message in a conversation."""

    role: MessageRole
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class UserContext:
    """Information about the human user participating in the session."""

    name: str | None = None
    language: str = "es"


@dataclass
class ToolCallResultMessage(ConversationMessage):
    """Specialized message that captures a tool call result.

    Using `role="tool"` makes it easy to adapt to frameworks that
    distinguish tool outputs from normal assistant messages.
    """

    tool_id: str | None = None
