"""Agent configuration from environment variables."""

from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    hub_url: str = "http://localhost:8000"
    redis_url: str = "redis://redis:6379/0"
    agent_token: str = ""
    agent_node_id: str = ""
    agent_name: str = "ikabot-agent"
    agent_version: str = "2.0.0"
    max_parallel: int = 12
    heartbeat_interval: int = 60
    log_level: str = "INFO"

    class Config:
        env_prefix = ""
        env_file = ".env"
        case_sensitive = False


settings = AgentSettings()
