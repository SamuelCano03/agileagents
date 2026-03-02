"""Minimal GitHub Models client used by local agents.

This client is intentionally small and provider-agnostic enough to work with
OpenAI-compatible chat completion endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests

from src.config.settings import settings
from src.conversations.messages import ConversationMessage


@dataclass
class GithubModelsClient:
    """Small wrapper for chat-completion style model calls."""

    endpoint: str
    model: str
    token: str
    temperature: float = 0.2
    max_tokens: int = 500
    timeout_seconds: int = 40

    @classmethod
    def from_settings(cls) -> "GithubModelsClient | None":
        """Build a client only if all required settings are available."""

        if settings.llm_mode != "github_models":
            return None

        if not settings.github_model or not settings.github_token:
            return None

        endpoint = settings.github_models_endpoint.strip()
        if not endpoint:
            return None

        return cls(
            endpoint=endpoint,
            model=settings.github_model,
            token=settings.github_token,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        history: Iterable[ConversationMessage],
        user_prompt: str,
    ) -> str | None:
        """Generate a model response from structured prompts.

        Returns None if the response does not contain content.
        Raises requests exceptions for transport-level failures.
        """

        messages = self._build_messages(
            system_prompt=system_prompt,
            history=history,
            user_prompt=user_prompt,
        )

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        response = requests.post(
            self.endpoint,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        # OpenAI-compatible extraction
        choices = data.get("choices") or []
        if choices:
            first = choices[0] or {}
            message = first.get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

        # Alternate schema fallback (future-proofing)
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        return None

    def _build_messages(
        self,
        *,
        system_prompt: str,
        history: Iterable[ConversationMessage],
        user_prompt: str,
    ) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        for item in history:
            if item.role not in {"system", "user", "assistant"}:
                continue
            text = (item.content or "").strip()
            if not text:
                continue
            msgs.append({"role": item.role, "content": text})

        msgs.append({"role": "user", "content": user_prompt})
        return msgs
