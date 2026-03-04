"""Product Owner agent definition."""

from __future__ import annotations

from typing import Any, Iterable

from src.agents.base_agent import BaseScrumAgent
from src.agents.roles import AgentRole
from src.conversations.messages import ConversationMessage, UserContext


PRODUCT_OWNER_PROMPT_ES = (
    "Eres un Product Owner centrado en maximizar el valor de negocio. "
    "Te preocupa que el equipo trabaje en los ítems de mayor impacto y "
    "que las dependencias con stakeholders estén claras."
)

PRODUCT_OWNER_PROMPT_EN = (
    "You are a Product Owner focused on maximizing business value. "
    "You care that the team works on the highest-impact items and that "
    "stakeholder expectations are clear."
)


class ProductOwnerAgent(BaseScrumAgent):
    """Agent representing the Product Owner role."""

    def __init__(self, language: str = "en") -> None:
        system_prompt = PRODUCT_OWNER_PROMPT_ES if language == "es" else PRODUCT_OWNER_PROMPT_EN
        super().__init__(role=AgentRole.PRODUCT_OWNER, language=language, system_prompt=system_prompt)

    def generate_reply(
        self,
        *,
        history: Iterable[ConversationMessage],
        user_context: UserContext,
        extra_context: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        """Placeholder implementation for the Product Owner's voice."""

        _ = history
        extra_context = extra_context or {}
        language = self.language

        if language == "es":
            default_content = (
                "Desde la perspectiva de negocio, quiero asegurarme de que "
                "los bloqueos en las historias de mayor prioridad se resuelvan pronto."
            )
        else:
            default_content = (
                "From a business perspective, I want to ensure that "
                "blockers on the highest-priority stories are resolved quickly."
            )

        content = extra_context.get("fallback_message", default_content)

        return ConversationMessage(role="assistant", content=content, metadata={"agent": self.role.value})
