"""Mapa da cidade igual ao do jogo, para o painel de reorganizar edificios (N-50).

Valores copiados do CSS da tela da cidade do Ikariam (compiled-br-city.css e
CSS do CDN, capturados em 2026-09-23 na HAVIT s78): a cidade tem 1920x1200 px
(4 blocos de fundo 960x600 que mudam com a fase / capital), cada posicao e uma
ancora de 86x43 px e a imagem do edificio fica deslocada da ancora (img_pos).
As imagens estao em static/game/city/.
"""

from __future__ import annotations

from django.templatetags.static import static

MAP_WIDTH = 1920
MAP_HEIGHT = 1200
ANCHOR = (86, 43)

# .positionN{left;top}
POSITIONS = {
    0: (884, 462), 1: (730, 738), 2: (1085, 717), 3: (1209, 535), 4: (1010, 556),
    5: (851, 596), 6: (649, 577), 7: (491, 537), 8: (490, 416), 9: (650, 457),
    10: (1051, 418), 11: (1207, 376), 12: (908, 312), 13: (770, 359), 14: (452, 284),
    15: (528, 717), 16: (1088, 319), 17: (1088, 892), 18: (1332, 585), 19: (1320, 203),
    20: (1490, 636), 21: (1442, 763), 22: (1319, 699), 23: (439, 634), 24: (573, 981),
}

DEFAULT_SIZE = (172, 140)

# building -> (file, left, top[, width, height])
SPRITES = {
    "townHall": ("townhall_l", -42, -65),
    "barracks": ("barracks_r", -55, -69),
    "pirateFortress": ("pirateFortress_l", -148, -75, 340, 250),
    "museum": ("museum_l", -48, -72),
    "warehouse": ("warehouse_l", -59, -68),
    "wall": ("wall", -43, -49, 201, 111),
    "tavern": ("taverne_l", -50, -69),
    "palace": ("palace_l", -50, -72),
    "palaceColony": ("palaceColony_l", -56, -68),
    "academy": ("academy_l", -50, -73),
    "workshop": ("workshop_l", -44, -69),
    "safehouse": ("safehouse_l", -46, -71),
    "temple": ("temple_l", -55, -71),
    "branchOffice": ("branchoffice_l", -48, -71),
    "embassy": ("embassy_l", -49, -74),
    "forester": ("forester_l", -48, -74),
    "stonemason": ("stonemason_l", -47, -70),
    "glassblowing": ("glassblowing_l", -43, -71),
    "winegrower": ("winegrower_l", -46, -69),
    "alchemist": ("alchemist_l", -47, -72),
    "carpentering": ("carpentering_l", -44, -71),
    "architect": ("architect_l", -47, -71),
    "optician": ("optician_l", -51, -66),
    "vineyard": ("vineyard_l", -45, -69),
    "fireworker": ("fireworker_l", -47, -67),
    "dump": ("dump_l", -46, -69),
    "blackMarket": ("blackmarket_l", -56, -58),
    "marineChartArchive": ("marinechartarchive_l", -60, -66),
    "shrineOfOlympus": ("shrineOfOlympus_l", -42, -62),
    "chronosForge": ("chronosForge_l", -45, -70),
}

# Port and shipyard face the water: different art on the two shore slots.
SHORE_SPRITES = {
    ("port", 1): ("port_r", -45, -48, 209, 148),
    ("port", 2): ("port_l", -57, -43, 209, 148),
    ("shipyard", 1): ("shipyard_r", -70, -64, 191, 126),
    ("shipyard", 2): ("shipyard_l", -70, -64, 191, 126),
}

# Empty slot flag by ground type (.buildingGround.<type>)
EMPTY_FLAGS = {"land": "flag_red", "shore": "flag_blue", "dockyard": "flag_blue", "wall": "flag_yellow", "sea": "flag_black"}
EMPTY_SPRITE = (-30, -25, 129, 78)
EMPTY_WALL_SPRITE = (-50, -31, 196, 99)

# groundId -> ground type (for empty slots without a stored type)
GROUND_TYPES = {0: "land", 1: "shore", 2: "land", 3: "wall", 5: "dockyard"}


def _sprite(file: str, left: int, top: int, width: int, height: int) -> dict:
    return {"src": static(f"game/city/b/{file}.png"), "x": left, "y": top, "w": width, "h": height}


def building_sprite(building: str, position: int, ground_type: str = "") -> dict | None:
    """Game art for `building` at `position` (None = no art, use the hub icon)."""
    if building == "empty":
        kind = ground_type or "land"
        left, top, width, height = EMPTY_WALL_SPRITE if kind == "wall" else EMPTY_SPRITE
        return _sprite(EMPTY_FLAGS.get(kind, "flag_red"), left, top, width, height)
    shore = SHORE_SPRITES.get((building, position))
    if shore:
        return _sprite(*shore)
    spec = SPRITES.get(building)
    if not spec:
        return None
    file, left, top, *size = spec
    width, height = size or DEFAULT_SIZE
    return _sprite(file, left, top, width, height)


def sprite_table() -> dict:
    """Everything the page needs to redraw a slot after a swap (same data as building_sprite)."""
    return {
        "anchors": {str(p): list(xy) for p, xy in POSITIONS.items()},
        "buildings": {name: building_sprite(name, -1) for name in SPRITES},
        "shore": {f"{name}|{pos}": building_sprite(name, pos) for name, pos in SHORE_SPRITES},
        "empty": {kind: building_sprite("empty", -1, kind) for kind in EMPTY_FLAGS},
        "grounds": {str(g): kind for g, kind in GROUND_TYPES.items()},
    }


def background_tiles(phase: int, is_capital: bool) -> list[dict]:
    """The 4 background tiles (960x600) for the city's phase."""
    phase = min(5, max(1, int(phase or 4)))
    suffix = "_capital" if is_capital else ""
    return [
        {"src": static(f"game/city/bg/phase{phase}{suffix}_{quad}.jpg"), "x": x, "y": y}
        for quad, x, y in (("nw", 0, 0), ("ne", 960, 0), ("sw", 0, 600), ("se", 960, 600))
    ]
