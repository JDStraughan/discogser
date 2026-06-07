"""Configuration loaded from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Current vision-capable Claude Sonnet (verified against the model catalog:
# active, 1M context, image input supported). Override via ANTHROPIC_MODEL.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_FOLDER = "Uncategorized"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    anthropic_model: str
    discogs_token: str
    discogs_username: str
    discogs_folder: str
    user_agent: str

    @classmethod
    def load(cls, env_file: str | os.PathLike[str] | None = None) -> "Config":
        # Load .env from the project directory (or an explicit path) without
        # clobbering variables already present in the real environment.
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

        def require(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise ConfigError(
                    f"Missing required config: {name}. "
                    f"Copy .env.example to .env and fill it in."
                )
            return value

        user_agent = os.environ.get("USER_AGENT", "").strip()
        if not user_agent:
            raise ConfigError(
                "USER_AGENT is required — Discogs throttles requests without a "
                "unique, descriptive User-Agent. See .env.example."
            )

        return cls(
            anthropic_api_key=require("ANTHROPIC_API_KEY"),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL,
            discogs_token=require("DISCOGS_TOKEN"),
            discogs_username=require("DISCOGS_USERNAME"),
            discogs_folder=os.environ.get("DISCOGS_FOLDER", "").strip() or DEFAULT_FOLDER,
            user_agent=user_agent,
        )
