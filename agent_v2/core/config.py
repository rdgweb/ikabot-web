"""Agent configuration from environment variables."""

import uuid
from pathlib import Path

from pydantic_settings import BaseSettings


def _read_version() -> str:
    """Le a versao do arquivo agent_v2/VERSION (fonte unica). Env AGENT_VERSION
    ainda sobrescreve. Assim a versao reportada acompanha o VERSION do repo."""
    try:
        text = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        return text or "0.0.0"
    except Exception:
        return "0.0.0"


class AgentSettings(BaseSettings):
    hub_url: str = "http://localhost:8000"
    redis_url: str = "redis://redis:6379/0"
    agent_token: str = ""
    agent_node_id: str = ""
    agent_name: str = "ikabot-agent"
    agent_version: str = _read_version()
    agent_image: str = ""
    agent_image_digest: str = ""
    max_parallel: int = 12
    heartbeat_interval: int = 60
    log_level: str = "INFO"
    # Blackbox (N-73): how long the agent waits for the hub to answer with the login
    # token, and how long it waits just to reach the hub. Every other hub call keeps
    # the short limit. Must stay below any reverse proxy in front of the hub (the
    # usual default there is 60s).
    blackbox_timeout: int = 55
    blackbox_connect_timeout: int = 5

    class Config:
        env_prefix = ""
        env_file = ".env"
        case_sensitive = False


settings = AgentSettings()

# Auto-generate node ID if not provided
if not settings.agent_node_id:
    settings.agent_node_id = str(uuid.uuid4())
