"""Military unit and fleet data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MilitaryUnit:
    """A group of land military units of the same type."""

    unit_type: str
    count: int

    def to_dict(self) -> dict:
        return {"unit_type": self.unit_type, "count": self.count}

    @classmethod
    def from_dict(cls, data: dict) -> MilitaryUnit:
        return cls(unit_type=data["unit_type"], count=int(data["count"]))


@dataclass
class Fleet:
    """A group of naval ships of the same type."""

    ship_type: str
    count: int

    def to_dict(self) -> dict:
        return {"ship_type": self.ship_type, "count": self.count}

    @classmethod
    def from_dict(cls, data: dict) -> Fleet:
        return cls(ship_type=data["ship_type"], count=int(data["count"]))
