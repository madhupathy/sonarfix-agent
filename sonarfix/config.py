"""Configuration via pydantic-settings, loaded from .env or environment."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SonarQube
    sonarqube_url: str = ""
    sonarqube_username: str = ""
    sonarqube_password: str = ""
    sonarqube_verify_ssl: bool = False

    # LLM-based fixer (vLLM on GPU server by default)
    llm_api_key: str = "dummy"
    llm_model: str = "Qwen/Qwen2.5-72B-Instruct-AWQ"
    llm_base_url: str = "http://localhost:8000/v1"
    llm_timeout: float = 600.0

    # SSL verification (set SSL_VERIFY=false to disable, e.g. for self-signed certs)
    ssl_verify: bool = True

    # Workspace
    workspace_dir: Path = Path.home() / ".sonarfix" / "workspaces"

    # Git
    git_push_remote: str = "origin"
    git_user_name: Optional[str] = None
    git_user_email: Optional[str] = None

    @property
    def sonarqube_auth(self) -> tuple[str, str]:
        return (self.sonarqube_username, self.sonarqube_password)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
