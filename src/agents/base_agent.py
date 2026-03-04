"""Base classes for Scrum agents using Microsoft Agent Framework.

This file intentionally does *not* depend on the concrete Microsoft
Agent Framework types yet. Instead it defines a minimal interface that
we can later adapt to the actual framework's Agent/Kernel classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from src.agents.roles import AgentRole
from src.conversations.messages import ConversationMessage, UserContext


class ToolInvoker(Protocol):
    """Protocol for tool invocation objects used by agents.

    In a real Microsoft Agent Framework setup, tools would be registered
    directly with the agent. Here we represent them as callables that
    agents *could* use when reasoning about Jira/GitHub state.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - interface only
        ...


@dataclass
class BaseScrumAgent:
    """Common functionality shared by all Scrum agents.

    Concrete subclasses should focus on defining their personality and
    how they transform context + history into messages.
    """

    role: AgentRole
    language: str = "en"
    tools: dict[str, ToolInvoker] = field(default_factory=dict)

    system_prompt: str = ""

    def configure_language(self, language: str) -> None:
        if language in ("es", "en"):
            self.language = language

    def attach_tools(self, tools: dict[str, ToolInvoker]) -> None:
        self.tools.update(tools)

    def build_system_message(self) -> ConversationMessage:
        """Return the system message that sets this agent's behavior."""

        return ConversationMessage(role="system", content=self.system_prompt)

    def generate_reply(
        self,
        *,
        history: Iterable[ConversationMessage],
        user_context: UserContext,
        extra_context: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        """Generate a reply message.

        This default implementation is intentionally naive and mainly
        serves as a placeholder until the Microsoft Agent Framework
        kernel is wired in. Subclasses are expected to override this.
        """

        _ = (history, user_context, extra_context)
        raise NotImplementedError("Subclasses must implement generate_reply().")
