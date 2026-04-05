"""Building data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Building:
    """Represents a building in a city slot."""

    position: int
    building_type: str
    level: int
    is_upgrading: bool = False
    upgrade_finish: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "position": self.position,
            "building_type": self.building_type,
            "level": self.level,
            "is_upgrading": self.is_upgrading,
            "upgrade_finish": self.upgrade_finish.isoformat() if self.upgrade_finish else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Building:
        finish = data.get("upgrade_finish")
        return cls(
            position=int(data["position"]),
            building_type=data["building_type"],
            level=int(data.get("level", 0)),
            is_upgrading=bool(data.get("is_upgrading", False)),
            upgrade_finish=datetime.fromisoformat(finish) if finish else None,
        )
