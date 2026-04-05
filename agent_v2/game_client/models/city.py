"""City and resource data models."""

from __future__ import annotations

from dataclasses import dataclass, field

from .building import Building


@dataclass
class Resources:
    """Current resource amounts for a city."""

    wood: int = 0
    wine: int = 0
    marble: int = 0
    crystal: int = 0
    sulfur: int = 0
    gold: int = 0

    def total(self) -> int:
        """Sum of all non-gold resources."""
        return self.wood + self.wine + self.marble + self.crystal + self.sulfur

    def to_dict(self) -> dict[str, int]:
        return {
            "wood": self.wood,
            "wine": self.wine,
            "marble": self.marble,
            "crystal": self.crystal,
            "sulfur": self.sulfur,
            "gold": self.gold,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Resources:
        return cls(
            wood=int(data.get("wood", 0)),
            wine=int(data.get("wine", 0)),
            marble=int(data.get("marble", 0)),
            crystal=int(data.get("crystal", 0)),
            sulfur=int(data.get("sulfur", 0)),
            gold=int(data.get("gold", 0)),
        )


@dataclass
class City:
    """Represents a player city in Ikariam."""

    id: int
    name: str
    island_id: int
    x: int
    y: int
    level: int
    population: int
    buildings: list[Building] = field(default_factory=list)
    resources: Resources = field(default_factory=Resources)

    def get_building(self, position: int) -> Building | None:
        """Get building at a specific position slot."""
        for b in self.buildings:
            if b.position == position:
                return b
        return None

    def get_buildings_by_type(self, building_type: str) -> list[Building]:
        """Get all buildings of a given type."""
        return [b for b in self.buildings if b.building_type == building_type]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "island_id": self.island_id,
            "x": self.x,
            "y": self.y,
            "level": self.level,
            "population": self.population,
            "buildings": [b.to_dict() for b in self.buildings],
            "resources": self.resources.to_dict(),
        }
