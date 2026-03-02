"""Team Member agent definition."""

from __future__ import annotations

from typing import Any, Iterable

from src.agents.base_agent import BaseScrumAgent
from src.agents.roles import AgentRole
from src.conversations.messages import ConversationMessage, UserContext
from src.llm.github_models_client import GithubModelsClient


TEAM_MEMBER_PROMPT_ES = (
    "Eres un desarrollador colaborativo. Debes reportar de forma clara "
    "qué hiciste ayer, qué harás hoy y si tienes bloqueos, usando un tono "
    "técnico pero entendible para negocio."
)

TEAM_MEMBER_PROMPT_EN = (
    "You are a collaborative developer. You should clearly report what "
    "you did yesterday, what you will do today, and any blockers, "
    "using a technical but business-friendly tone."
)


class TeamMemberAgent(BaseScrumAgent):
    """Agent representing a generic team member."""

    def __init__(self, name: str, language: str = "es") -> None:
        system_prompt = TEAM_MEMBER_PROMPT_ES if language == "es" else TEAM_MEMBER_PROMPT_EN
        super().__init__(role=AgentRole.TEAM_MEMBER, language=language, system_prompt=system_prompt)
        self.name = name
        self._llm_client = GithubModelsClient.from_settings()

    def generate_reply(
        self,
        *,
        history: Iterable[ConversationMessage],
        user_context: UserContext,
        extra_context: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        """Placeholder implementation that summarizes basic progress.

        Later this method will:
        - use tools to inspect Jira issues assigned to this member
        - synthesize a proper stand-up style update.
        """

        extra_context = extra_context or {}
        language = self.language

        yesterday = extra_context.get("yesterday", "trabajé en varias tareas del sprint")
        today = extra_context.get("today", "seguiré avanzando en las mismas historias")
        blockers = extra_context.get("blockers", "no tengo bloqueos relevantes")

        if language == "en":
            yesterday = extra_context.get("yesterday", "I worked on several sprint tasks")
            today = extra_context.get("today", "I will keep making progress on the same stories")
            blockers = extra_context.get("blockers", "I have no relevant blockers")

        if language == "es":
            fallback_content = f"Ayer {yesterday} Hoy {today} En cuanto a bloqueos: {blockers}."
        else:
            fallback_content = f"Yesterday {yesterday} Today {today} Regarding blockers: {blockers}."

        if self._llm_client is not None:
            prompt = self._build_dynamic_prompt(
                fallback_message=fallback_content,
                user_context=user_context,
                extra_context=extra_context,
            )
            try:
                llm_content = self._llm_client.generate(
                    system_prompt=self.system_prompt,
                    history=history,
                    user_prompt=prompt,
                )
                if llm_content:
                    return ConversationMessage(
                        role="assistant",
                        content=llm_content,
                        metadata={
                            "agent": self.role.value,
                            "name": self.name,
                            "user": user_context.name,
                            "mode": "github_models",
                        },
                    )
            except Exception:
                pass

        content = fallback_content

        return ConversationMessage(
            role="assistant",
            content=content,
            metadata={"agent": self.role.value, "name": self.name, "user": user_context.name},
        )

    def _build_dynamic_prompt(
        self,
        *,
        fallback_message: str,
        user_context: UserContext,
        extra_context: dict[str, Any],
    ) -> str:
        language = "español" if self.language == "es" else "english"
        _ = user_context

        context_parts: list[str] = []
        for key, value in extra_context.items():
            context_parts.append(f"- {key}: {value}")

        context_block = "\n".join(context_parts) if context_parts else "- (no extra context)"

        return (
            f"Respond as a team member in {language}.\n"
            f"Your name: {self.name}.\n"
            "Use first person and keep it concise (2-4 sentences).\n"
            "Do not directly address another participant by name unless explicitly asked.\n"
            "If blockers or dependencies exist, mention a concrete next action.\n\n"
            f"Fallback intent:\n{fallback_message}\n\n"
            f"Context:\n{context_block}"
        )
