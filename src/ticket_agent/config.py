"""Central config, loaded from environment variables (.env in dev)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    github_token: str
    github_repo: str
    agent_model: str
    db_path: str = "ticket_agent.db"

    @classmethod
    def load(cls) -> "Settings":
        missing = [
            name
            for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )
        return cls(
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            github_token=os.environ["GITHUB_TOKEN"],
            github_repo=os.environ["GITHUB_REPO"],
            agent_model=os.getenv("AGENT_MODEL", "claude-sonnet-4-5"),
        )


settings = None  # populated lazily via get_settings() to keep imports side-effect-free


def get_settings() -> Settings:
    global settings
    if settings is None:
        settings = Settings.load()
    return settings
