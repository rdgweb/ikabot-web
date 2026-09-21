"""Payload accepted by the telemetry receiver (schema v1).

Anything not declared here is dropped, never stored. Keep this file in sync with
``static/transparencia.html`` and ``hub_v2/apps/telemetry/services.py``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _Model(BaseModel):
    # Unknown keys are ignored (dropped), so a future/modified client can never
    # push extra data into the database.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Counts(_Model):
    lobby_accounts: int = Field(0, ge=0, le=100_000)
    game_accounts: int = Field(0, ge=0, le=100_000)
    nodes: int = Field(0, ge=0, le=10_000)


class AgentVersion(_Model):
    version: str = Field(pattern=r"^[0-9A-Za-z.\-+]{1,32}$")
    nodes: int = Field(ge=0, le=10_000)


class WorldUsage(_Model):
    server: str = Field(pattern=r"^[a-z]{2,3}$")  # Ikariam server language, e.g. "br"
    world: int = Field(ge=1, le=9_999)  # world number, e.g. 61
    accounts: int = Field(ge=0, le=100_000)


class CategoryUsage(_Model):
    category: str = Field(pattern=r"^[a-z_]{1,32}$")
    jobs: int = Field(ge=0, le=100_000_000)


class Ping(_Model):
    schema_version: int = Field(alias="schema")
    install_id: UUID
    hub_version: str = Field(pattern=r"^[0-9A-Za-z.\-+]{1,32}$")
    arch: Literal["amd64", "arm64", "other"] = "other"
    timezone: str = Field(default="", max_length=64)
    counts: Counts = Field(default_factory=Counts)
    agents: list[AgentVersion] = Field(default_factory=list, max_length=200)
    worlds: list[WorldUsage] = Field(default_factory=list, max_length=500)
    usage_30d: list[CategoryUsage] = Field(default_factory=list, max_length=32)
