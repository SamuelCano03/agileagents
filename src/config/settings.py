"""Global configuration for the Agile Agents project.

This module centralizes environment-based configuration so that
no secrets are hard-coded in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal


LanguageCode = Literal["es", "en"]
LlmMode = Literal["none", "github_models"]


@dataclass
class Settings:
    """Runtime settings loaded from environment variables.

    All sensitive values should come from process environment
    (potentially loaded from a local .env file that is *not* committed).
    """

    # Language
    default_language: LanguageCode = "es"

    # Jira
    jira_base_url: str | None = None
    jira_project_key: str | None = None
    jira_api_token: str | None = None
    jira_email: str | None = None

    # GitHub
    github_model: str | None = None
    github_token: str | None = None
    github_models_endpoint: str = "https://models.inference.ai.azure.com/chat/completions"

    # LLM runtime
    llm_mode: LlmMode = "none"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 500

    # MCP server
    mcp_jira_github_endpoint: str | None = None
    mcp_env: str | None = None

    # Misc
    app_env: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        """Create a Settings instance from os.environ.

        This keeps all env access in one place, which makes
        testing and future validation easier.
        """

        language = os.getenv("DEFAULT_LANGUAGE", "es")
        if language not in ("es", "en"):
            language = "es"

        llm_mode = os.getenv("LLM_MODE", "none").strip().lower()
        if llm_mode not in ("none", "github_models"):
            llm_mode = "none"

        try:
            llm_temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        except ValueError:
            llm_temperature = 0.2

        try:
            llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "500"))
        except ValueError:
            llm_max_tokens = 500

        return cls(
            default_language=language,  # type: ignore[arg-type]
            jira_base_url=os.getenv("JIRA_BASE_URL"),
            jira_project_key=os.getenv("JIRA_PROJECT_KEY"),
            jira_api_token=os.getenv("JIRA_API_TOKEN"),
            jira_email=os.getenv("JIRA_EMAIL"),
            github_model=os.getenv("GITHUB_MODEL"),
            github_token=os.getenv("GITHUB_TOKEN"),
            github_models_endpoint=os.getenv(
                "GITHUB_MODELS_ENDPOINT",
                "https://models.inference.ai.azure.com/chat/completions",
            ),
            llm_mode=llm_mode,  # type: ignore[arg-type]
            llm_temperature=llm_temperature,
            llm_max_tokens=llm_max_tokens,
            mcp_jira_github_endpoint=os.getenv("MCP_JIRA_GITHUB_ENDPOINT"),
            mcp_env=os.getenv("MCP_ENV"),
            app_env=os.getenv("APP_ENV", "dev"),
        )


# A simple convenience accessor used by small scripts.
settings = Settings.from_env()
