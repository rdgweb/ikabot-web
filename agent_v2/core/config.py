"""Agent configuration from environment variables."""

import uuid

from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    hub_url: str = "http://localhost:8000"
    redis_url: str = "redis://redis:6379/0"
    agent_token: str = ""
    agent_node_id: str = ""
    agent_name: str = "ikabot-agent"
    agent_version: str = "0.0.29"
    agent_image: str = ""
    agent_image_digest: str = ""
    max_parallel: int = 12
    heartbeat_interval: int = 60
    log_level: str = "INFO"

    class Config:
        env_prefix = ""
        env_file = ".env"
        case_sensitive = False


settings = AgentSettings()

# Auto-generate node ID if not provided
if not settings.agent_node_id:
    settings.agent_node_id = str(uuid.uuid4())
