"""Scrum Master agent definition."""

from __future__ import annotations

from typing import Any, Iterable

from src.agents.base_agent import BaseScrumAgent
from src.agents.roles import AgentRole
from src.conversations.messages import ConversationMessage, UserContext
from src.llm.github_models_client import GithubModelsClient


SCRUM_MASTER_PROMPT_ES = (
    "Eres un Scrum Master experimentado. Tu prioridad es identificar y "
    "eliminar bloqueos, mejorar el flujo de trabajo del equipo y asegurar "
    "que la daily sea breve y enfocada."
)

SCRUM_MASTER_PROMPT_EN = (
    "You are an experienced Scrum Master. Your priority is to spot and "
    "remove blockers, improve the team's flow, and keep the stand-up "
    "short and focused."
)


class ScrumMasterAgent(BaseScrumAgent):
    """Agent representing the Scrum Master role."""

    def __init__(self, language: str = "en") -> None:
        system_prompt = SCRUM_MASTER_PROMPT_ES if language == "es" else SCRUM_MASTER_PROMPT_EN
        super().__init__(role=AgentRole.SCRUM_MASTER, language=language, system_prompt=system_prompt)
        self._llm_client = GithubModelsClient.from_settings()

    def generate_reply(
        self,
        *,
        history: Iterable[ConversationMessage],
        user_context: UserContext,
        extra_context: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        """Very small placeholder implementation.

        Later this method will delegate to the Microsoft Agent Framework
        kernel/agent using `self.system_prompt` and the provided history.
        """

        language = self.language
        extra_context = extra_context or {}

        # Baseline fallback content used when LLM is disabled or fails.
        if language == "es":
            fallback_content = extra_context.get(
                "fallback_message",
                "Vamos a mantener la daily enfocada. ¿Quién quiere empezar a compartir sus avances?",
            )
        else:
            fallback_content = extra_context.get(
                "fallback_message",
                "Let's keep this stand-up focused. Who would like to start sharing their updates?",
            )

        # If LLM is configured, generate a richer response using context and history.
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
                        metadata={"agent": self.role.value, "mode": "github_models"},
                    )
            except Exception:
                # Silent fallback for resilience in local demos.
                pass

        content = fallback_content
        return ConversationMessage(role="assistant", content=content, metadata={"agent": self.role.value})

    def _build_dynamic_prompt(
        self,
        *,
        fallback_message: str,
        user_context: UserContext,
        extra_context: dict[str, Any],
    ) -> str:
        """Compose an explicit instruction for open-ended LLM replies."""

        language = "español" if self.language == "es" else "english"
        person = user_context.name or "team member"

        # Keep prompt concise but contextual.
        context_parts: list[str] = []
        for key, value in extra_context.items():
            if key == "fallback_message":
                continue
            context_parts.append(f"- {key}: {value}")

        context_block = "\n".join(context_parts) if context_parts else "- (no extra context)"

        return (
            f"Respond as the Scrum Master in {language}.\n"
            f"User/team member name: {person}.\n"
            "Use the context below and provide a practical, concise answer. "
            "Do not invent Jira issues that were not mentioned.\n\n"
            f"Primary intent to address:\n{fallback_message}\n\n"
            f"Additional context:\n{context_block}\n\n"
            "If there is a risk or dependency, suggest a concrete next action."
        )
