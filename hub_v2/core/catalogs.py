"""
Building and unit catalogs used by the hub UI.
"""

import unicodedata
# ── Building catalog ──
BUILDING_CATALOG: dict[str, dict] = {
    # Core
    "townHall":     {"name": "Prefeitura",          "name_en": "Town Hall",         "icon": "townhall.png",  "bi": None},
    "palace":       {"name": "Palacio",             "name_en": "Palace",            "icon": "palace.png",    "bi": None},
    "governorsResidence": {"name": "Residencia do Governador", "name_en": "Governor's Residence", "icon": "governorsresidence.png", "bi": None},
    "tavern":       {"name": "Taverna",             "name_en": "Tavern",            "icon": "tavern.png",    "bi": None},
    "museum":       {"name": "Museu",               "name_en": "Museum",            "icon": "museum.png",    "bi": None},
    "academy":      {"name": "Academia",            "name_en": "Academy",           "icon": "academy.png",   "bi": None},
    "warehouse":    {"name": "Armazem",             "name_en": "Warehouse",         "icon": "warehouse.png", "bi": None},
    "dump":         {"name": "Deposito",            "name_en": "Dump",              "icon": "depot.png",     "bi": None},
    # Military
    "barracks":     {"name": "Quartel",             "name_en": "Barracks",          "icon": "barracks.png",  "bi": None},
    "shipyard":     {"name": "Estaleiro",           "name_en": "Shipyard",          "icon": "shipyard.png",  "bi": "bi-anchor"},
    "wall":         {"name": "Muralha",             "name_en": "Wall",              "icon": "wall.png",      "bi": None},
    "hideout":      {"name": "Esconderijo",         "name_en": "Hideout",           "icon": "safehouse.png", "bi": None},
    "safehouse":    {"name": "Esconderijo",         "name_en": "Safehouse",         "icon": "safehouse.png", "bi": None},
    # Port & Trade
    "port":         {"name": "Porto",               "name_en": "Trading Port",      "icon": "port.png",      "bi": None},
    "brpiort":      {"name": "Porto",               "name_en": "Trading Port",      "icon": "port.png",      "bi": None},
    # Resources
    "forpiester":   {"name": "Floresta",            "name_en": "Forester's House",  "icon": "forestershouse.png", "bi": "bi-tree-fill"},
    "carpentering": {"name": "Carpintaria",         "name_en": "Carpenter's Workshop", "icon": "carpenter.png", "bi": "bi-tools"},
    "alchemist":    {"name": "Torre do Alquimista", "name_en": "Alchemist's Tower", "icon": "alchemiststower.png", "bi": "bi-flask"},
    "forester":     {"name": "Floresta",            "name_en": "Forester's House",  "icon": "forestershouse.png", "bi": "bi-tree-fill"},
    "winpiegrower": {"name": "Cave de Vinho",       "name_en": "Wine Press",        "icon": "winepress.png", "bi": "bi-cup-fill"},
    "winegrower":   {"name": "Vinhedos",            "name_en": "Winegrower",        "icon": "winegrower.png", "bi": "bi-cup-fill"},
    "vineyard":     {"name": "Cave de Vinho",       "name_en": "Vineyard",          "icon": "winepress.png", "bi": "bi-cup-fill"},
    "architect":    {"name": "Escritorio do Arquiteto", "name_en": "Architect's Office", "icon": "architectsoffice.png", "bi": "bi-pencil-ruler"},
    "glassblowing": {"name": "Soprador de Vidro",   "name_en": "Glassblower",       "icon": "glassblower.png", "bi": "bi-gem"},
    "optician":     {"name": "Oculista",            "name_en": "Optician",          "icon": "optician.png",  "bi": "bi-eyeglasses"},
    "fireworker":   {"name": "Pirotecnico",         "name_en": "Firework Test Area","icon": "fireworktestarea.png", "bi": "bi-fire"},
    "firpieworker": {"name": "Pirotecnico",         "name_en": "Firework Test Area","icon": "fireworktestarea.png", "bi": "bi-fire"},
    "stonemason":   {"name": "Pedreiro",            "name_en": "Stonemason",        "icon": "stonemason.png", "bi": "bi-bricks"},
    # Special
    "temple":       {"name": "Templo",              "name_en": "Temple",            "icon": "temple.png",   "bi": "bi-columns"},
    "embassy":      {"name": "Embaixada",           "name_en": "Embassy",           "icon": "embassy.png",  "bi": "bi-flag-fill"},
    "workshop":     {"name": "Oficina do Inventor", "name_en": "Workshop",          "icon": "workshop.png", "bi": "bi-wrench-adjustable"},
    "chronos_forge": {"name": "Forja de Chronos",   "name_en": "Chronos Forge",     "icon": "chronos_forge.png", "bi": "bi-hourglass-split"},
    "chronosForge": {"name": "Forja de Chronos",    "name_en": "Chronos Forge",     "icon": "chronos_forge.png", "bi": "bi-hourglass-split"},
    "marineChartArchive": {"name": "Arquivo de Cartas Nauticas", "name_en": "Sea Chart Archive", "icon": "seachartarchive.png", "bi": "bi-compass"},
    "pirateFortress": {"name": "Fortaleza Pirata",  "name_en": "Pirate Fortress",   "icon": "piratefortress.png", "bi": "bi-shield-fill-exclamation"},
    "pipiracy":     {"name": "Fortaleza Pirata",    "name_en": "Pirate Fortress",   "icon": "piratefortress.png", "bi": "bi-shield-fill-exclamation"},
    "blackMarket":  {"name": "Mercado Negro",       "name_en": "Black Market",      "icon": "blackmarket.png", "bi": "bi-bag-dash-fill"},
    "branchOffice": {"name": "Mercado",             "name_en": "Branch Office",     "icon": "tradingpost.png", "bi": "bi-shop"},
    "dockyard":     {"name": "Estaleiro Comercial", "name_en": "Dockyard",          "icon": "shipyard.png", "bi": "bi-water"},
    "shrineOfOlympus": {"name": "Santuario dos Deuses", "name_en": "Shrine of Olympus", "icon": "temple.png", "bi": "bi-stars"},
    "marpiket":     {"name": "Mercado",             "name_en": "Marketplace",       "icon": "tradingpost.png", "bi": "bi-shop"},
}


# ── Unit catalog ──
UNIT_CATALOG: dict[str, dict] = {
    # Land troops (by name)
    "Hoplite":          {"name": "Hoplita",             "type": "troop"},
    "Swordsman":        {"name": "Espadachim",          "type": "troop"},
    "Slinger":          {"name": "Fundeiro",            "type": "troop"},
    "Archer":           {"name": "Arqueiro",            "type": "troop"},
    "Spearman":         {"name": "Lanceiro",            "type": "troop"},
    "Sulphur_Carabineer": {"name": "Carabineiro",      "type": "troop"},
    "Ram":              {"name": "Ariete",              "type": "troop"},
    "Catapult":         {"name": "Catapulta",           "type": "troop"},
    "Mortar":           {"name": "Morteiro",            "type": "troop"},
    "Gyrocopter":       {"name": "Girocopter",          "type": "troop"},
    "Balloon_Bombardier": {"name": "Bombardeiro",       "type": "troop"},
    "Cook":             {"name": "Cozinheiro",          "type": "troop"},
    "Doctor":           {"name": "Medico",              "type": "troop"},
    "Spartan":          {"name": "Espartano",           "type": "troop"},
    # Land troops (by CSS class ID from game HTML)
    "s301":             {"name": "Fundeiro",            "type": "troop"},
    "s302":             {"name": "Espadachim",          "type": "troop"},
    "s303":             {"name": "Hoplita",             "type": "troop"},
    "s304":             {"name": "Lanceiro",            "type": "troop"},
    "s305":             {"name": "Arqueiro",            "type": "troop"},
    "s306":             {"name": "Carabineiro",         "type": "troop"},
    "s307":             {"name": "Ariete",              "type": "troop"},
    "s308":             {"name": "Catapulta",           "type": "troop"},
    "s309":             {"name": "Morteiro",            "type": "troop"},
    "s310":             {"name": "Girocopter",          "type": "troop"},
    "s311":             {"name": "Bombardeiro",         "type": "troop"},
    "s312":             {"name": "Cozinheiro",          "type": "troop"},
    "s313":             {"name": "Medico",              "type": "troop"},
    "s315":             {"name": "Espartano",           "type": "troop"},
    # Fleet (by name)
    "Ram_Ship":         {"name": "Navio Ariete",        "type": "ship"},
    "Fire_Ship":        {"name": "Navio de Fogo",       "type": "ship"},
    "Ballista_Ship":    {"name": "Navio Balista",       "type": "ship"},
    "Catapult_Ship":    {"name": "Navio Catapulta",     "type": "ship"},
    "Mortar_Ship":      {"name": "Navio Morteiro",      "type": "ship"},
    "Steam_Ram":        {"name": "Ariete a Vapor",      "type": "ship"},
    "Rocket_Ship":      {"name": "Navio Foguete",       "type": "ship"},
    "Diving_Boat":      {"name": "Submarino",           "type": "ship"},
    "Paddle_Speedboat": {"name": "Lancha Rapida",       "type": "ship"},
    "Tender":           {"name": "Navio Tender",        "type": "ship"},
    # Fleet (by CSS class ID)
    "s201":             {"name": "Navio Ariete",        "type": "ship"},
    "s202":             {"name": "Navio Balista",       "type": "ship"},
    "s203":             {"name": "Navio de Fogo",       "type": "ship"},
    "s204":             {"name": "Navio Catapulta",     "type": "ship"},
    "s205":             {"name": "Navio Morteiro",      "type": "ship"},
    "s206":             {"name": "Submarino",           "type": "ship"},
    "s207":             {"name": "Lancha Rapida",       "type": "ship"},
    "s210":             {"name": "Navio Tender",        "type": "ship"},
    "s211":             {"name": "Navio Foguete",       "type": "ship"},
    "s212":             {"name": "Ariete a Vapor",      "type": "ship"},
    "s213":             {"name": "Navio Lanceiro",      "type": "ship"},
    # Localized aliases seen in snapshots
    "Hoplita":          {"name": "Hoplita",             "type": "troop"},
    "Espadachim":       {"name": "Espadachim",          "type": "troop"},
    "Fundeiro":         {"name": "Fundeiro",            "type": "troop"},
    "Arqueiro":         {"name": "Arqueiro",            "type": "troop"},
    "Lanceiro":         {"name": "Lanceiro",            "type": "troop"},
    "Carabineiro":      {"name": "Carabineiro",         "type": "troop"},
    "Ariete":           {"name": "Ariete",              "type": "troop"},
    "Catapulta":        {"name": "Catapulta",           "type": "troop"},
    "Morteiro":         {"name": "Morteiro",            "type": "troop"},
    "Girocopter":       {"name": "Girocopter",          "type": "troop"},
    "Bombardeiro":      {"name": "Bombardeiro",         "type": "troop"},
    "Cozinheiro":       {"name": "Cozinheiro",          "type": "troop"},
    "Medico":           {"name": "Medico",              "type": "troop"},
    "Médico":           {"name": "Médico",              "type": "troop"},
    "Espartano":        {"name": "Espartano",           "type": "troop"},
    "Navio Ariete":     {"name": "Navio Ariete",        "type": "ship"},
    "Navio de Fogo":    {"name": "Navio de Fogo",       "type": "ship"},
    "Lanca-Chamas":     {"name": "Lanca-Chamas",        "type": "ship"},
    "Lança-Chamas":     {"name": "Lança-Chamas",        "type": "ship"},
    "Navio Balista":    {"name": "Navio Balista",       "type": "ship"},
    "Navio Catapulta":  {"name": "Navio Catapulta",     "type": "ship"},
    "Navio Morteiro":   {"name": "Navio Morteiro",      "type": "ship"},
    "Ariete a Vapor":   {"name": "Ariete a Vapor",      "type": "ship"},
    "Navio Foguete":    {"name": "Navio Foguete",       "type": "ship"},
    "Submarino":        {"name": "Submarino",           "type": "ship"},
    "Lancha Rapida":    {"name": "Lancha Rapida",       "type": "ship"},
    "Lancha Rápida":    {"name": "Lancha Rápida",       "type": "ship"},
    "Navio Tender":     {"name": "Navio Tender",        "type": "ship"},
    "Navio Lanceiro":   {"name": "Navio Lanceiro",      "type": "ship"},
}


DEFAULT_UNIT_ICONS = {
    "troop": "game/units/hoplita.png",
    "ship": "game/units/trieme.png",
}


UNIT_ICON_MAP: dict[str, str] = {
    "Hoplite": "game/units/hoplita.png",
    "Hoplita": "game/units/hoplita.png",
    "s303": "game/units/hoplita.png",
    "Swordsman": "game/units/espadachim.png",
    "Espadachim": "game/units/espadachim.png",
    "s302": "game/units/espadachim.png",
    "Slinger": "game/units/fundeiro.png",
    "Fundeiro": "game/units/fundeiro.png",
    "s301": "game/units/fundeiro.png",
    "Archer": "game/units/arqueiro.png",
    "Arqueiro": "game/units/arqueiro.png",
    "s305": "game/units/arqueiro.png",
    "Spearman": "game/units/lanceiro.png",
    "Lanceiro": "game/units/lanceiro.png",
    "s304": "game/units/lanceiro.png",
    "Sulphur_Carabineer": "game/units/atirador.png",
    "Carabineiro": "game/units/atirador.png",
    "s306": "game/units/atirador.png",
    "Ram": "game/units/ariete.png",
    "Ariete": "game/units/ariete.png",
    "s307": "game/units/ariete.png",
    "Catapult": "game/units/catapulta.png",
    "Catapulta": "game/units/catapulta.png",
    "s308": "game/units/catapulta.png",
    "Mortar": "game/units/morteiro.png",
    "Morteiro": "game/units/morteiro.png",
    "s309": "game/units/morteiro.png",
    "Gyrocopter": "game/units/girocoptero.png",
    "Girocopter": "game/units/girocoptero.png",
    "s310": "game/units/girocoptero.png",
    "Balloon_Bombardier": "game/units/balao.png",
    "Bombardeiro": "game/units/balao.png",
    "s311": "game/units/balao.png",
    "Cook": "game/units/cozinheiro.png",
    "Cozinheiro": "game/units/cozinheiro.png",
    "s312": "game/units/cozinheiro.png",
    "Doctor": "game/units/medico.png",
    "Medico": "game/units/medico.png",
    "Médico": "game/units/medico.png",
    "s313": "game/units/medico.png",
    "Spartan": "game/units/Porta-bal?es.png",
    "Espartano": "game/units/Porta-bal?es.png",
    "s315": "game/units/Porta-bal?es.png",
    "Ram_Ship": "game/units/trieme.png",
    "Navio Ariete": "game/units/trieme.png",
    "s201": "game/units/trieme.png",
    "Ballista_Ship": "game/units/Barco Balista.png",
    "Navio Balista": "game/units/Barco Balista.png",
    "s202": "game/units/Barco Balista.png",
    "Fire_Ship": "game/units/lancachamas.png",
    "Navio de Fogo": "game/units/lancachamas.png",
    "Lanca-Chamas": "game/units/lancachamas.png",
    "Lança-Chamas": "game/units/lancachamas.png",
    "s203": "game/units/lancachamas.png",
    "Catapult_Ship": "game/units/barcocatapulta.png",
    "Navio Catapulta": "game/units/barcocatapulta.png",
    "s204": "game/units/barcocatapulta.png",
    "Mortar_Ship": "game/units/barcomorteiro.png",
    "Navio Morteiro": "game/units/barcomorteiro.png",
    "s205": "game/units/barcomorteiro.png",
    "Diving_Boat": "game/units/Submergível.png",
    "Submarino": "game/units/Submergível.png",
    "s206": "game/units/Submergível.png",
    "Paddle_Speedboat": "game/units/Lancha Rápida.png",
    "Lancha Rapida": "game/units/Lancha Rápida.png",
    "Lancha Rápida": "game/units/Lancha Rápida.png",
    "s207": "game/units/Lancha Rápida.png",
    "Tender": "game/units/barcoreparador.png",
    "Navio Tender": "game/units/barcoreparador.png",
    "s210": "game/units/barcoreparador.png",
    "Rocket_Ship": "game/units/Lança-Foguetes.png",
    "Navio Foguete": "game/units/Lança-Foguetes.png",
    "s211": "game/units/Lança-Foguetes.png",
    "Steam_Ram": "game/units/Aríete a Vapor.png",
    "Ariete a Vapor": "game/units/Aríete a Vapor.png",
    "s212": "game/units/Aríete a Vapor.png",
    "Navio Lanceiro": "game/units/Lancha Rápida.png",
    "s213": "game/units/Lancha Rápida.png",
}

UNIT_CATALOG.update({
    "Gigante A Vapor": {"name": "Gigante a Vapor", "type": "troop"},
    "Gigante a Vapor": {"name": "Gigante a Vapor", "type": "troop"},
    "Steam_Giant": {"name": "Gigante a Vapor", "type": "troop"},
    "Steam Giant": {"name": "Gigante a Vapor", "type": "troop"},
    "Girocoptero": {"name": "Girocoptero", "type": "troop"},
    "Balao-Bombardeiro": {"name": "Balao-Bombardeiro", "type": "troop"},
    "Ariete A Vapor": {"name": "Ariete a Vapor", "type": "ship"},
})

UNIT_ICON_MAP.update({
    "Gigante A Vapor": "game/units/gigante.png",
    "Gigante a Vapor": "game/units/gigante.png",
    "Steam_Giant": "game/units/gigante.png",
    "Steam Giant": "game/units/gigante.png",
    "Girocoptero": "game/units/girocoptero.png",
    "Balao-Bombardeiro": "game/units/balao.png",
    "Ariete A Vapor": "game/units/Ar?ete a Vapor.png",
})

SHIP_UNIT_KEYS = {
    key for key, value in UNIT_CATALOG.items() if value.get("type") == "ship"
}

# Numeric unit ID → training metadata
# Names and IDs from live game data. CSS class = "s{id}".
# Costs are base values (before research reductions).
TRAINING_UNITS: dict[str, list[dict]] = {
    "troops": [
        {"id": 303, "name": "Hoplita",             "css": "s303", "wood": 20,  "sulfur": 14,  "upkeep": 3},
        {"id": 302, "name": "Espadachim",           "css": "s302", "wood": 15,  "sulfur": 14,  "upkeep": 4},
        {"id": 301, "name": "Fundeiro",             "css": "s301", "wood": 10,  "upkeep": 2},
        {"id": 315, "name": "Lanceiro",             "css": "s315", "wood": 15,  "upkeep": 1},
        {"id": 313, "name": "Arqueiro",             "css": "s313", "wood": 15,  "sulfur": 12,  "upkeep": 4},
        {"id": 304, "name": "Carabineiro",          "css": "s306", "wood": 25,  "sulfur": 73,  "upkeep": 3},
        {"id": 307, "name": "Aríete",               "css": "s307", "wood": 110, "upkeep": 15},
        {"id": 306, "name": "Catapulta",            "css": "s308", "wood": 130, "sulfur": 146, "upkeep": 25},
        {"id": 308, "name": "Gigante a Vapor",      "css": "s308", "wood": 65,  "sulfur": 87,  "upkeep": 12},
        {"id": 305, "name": "Morteiro",             "css": "s305", "wood": 150, "sulfur": 609, "upkeep": 30},
        {"id": 312, "name": "Girocóptero",          "css": "s310", "wood": 12,  "sulfur": 48,  "upkeep": 15},
        {"id": 309, "name": "Balão-Bombardeiro",    "css": "s309", "wood": 20,  "sulfur": 121, "upkeep": 45},
        {"id": 310, "name": "Cozinheiro",           "css": "s312", "wood": 25,  "wine": 73,    "upkeep": 10},
        {"id": 311, "name": "Médico",               "css": "s313", "wood": 25,  "crystal": 222,"upkeep": 20},
    ],
    "fleet": [
        {"id": 210, "name": "Trireme",              "css": "s210", "wood": 167, "upkeep": 15},
        {"id": 211, "name": "Lança-Chamas",         "css": "s211", "wood": 53,  "sulfur": 156, "upkeep": 25},
        {"id": 213, "name": "Barco Balista",        "css": "s202", "wood": 120, "sulfur": 108, "upkeep": 20},
        {"id": 214, "name": "Barco Catapulta",      "css": "s204", "wood": 120, "sulfur": 95,  "upkeep": 35},
        {"id": 215, "name": "Barco Morteiro",       "css": "s205", "wood": 147, "sulfur": 612, "upkeep": 50},
        {"id": 216, "name": "Aríete a Vapor",       "css": "s212", "wood": 268, "sulfur": 544, "upkeep": 45},
        {"id": 217, "name": "Lança-Foguetes",       "css": "s211", "wood": 134, "sulfur": 815, "upkeep": 55},
        {"id": 212, "name": "Submergível",          "css": "s206", "wood": 107, "crystal": 502,"sulfur": 68, "upkeep": 50},
        {"id": 218, "name": "Lancha Rápida",        "css": "s207", "wood": 26,  "sulfur": 190, "upkeep": 5},
        {"id": 219, "name": "Porta-balões",         "css": "s204", "wood": 468, "sulfur": 475, "upkeep": 100},
    ],
}


def get_unit_info(unit_name: str) -> dict:
    """Return display info for a unit name."""
    normalized_name = "".join(
        char for char in unicodedata.normalize("NFKD", unit_name)
        if not unicodedata.combining(char)
    )
    info = UNIT_CATALOG.get(unit_name) or UNIT_CATALOG.get(normalized_name)
    if info:
        return {
            **info,
            "icon": (
                UNIT_ICON_MAP.get(unit_name)
                or UNIT_ICON_MAP.get(normalized_name)
                or DEFAULT_UNIT_ICONS.get(info.get("type", "troop"))
            ),
        }
    # Fallback: clean up the name
    name = unit_name.replace("_", " ").title()
    unit_type = "ship" if unit_name in SHIP_UNIT_KEYS or normalized_name in SHIP_UNIT_KEYS else "troop"
    return {
        "name": name,
        "type": unit_type,
        "icon": UNIT_ICON_MAP.get(unit_name) or UNIT_ICON_MAP.get(normalized_name) or DEFAULT_UNIT_ICONS[unit_type],
    }


def get_building_info(building_id: str) -> dict:
    """Return display info for a building ID."""
    key = str(building_id or "").strip().split()[0]
    aliases = {
        "chronosForge": "chronos_forge",
        "palaceColony": "governorsResidence",
        "marketplace": "branchOffice",
    }
    normalized = aliases.get(key, key)
    info = BUILDING_CATALOG.get(normalized) or BUILDING_CATALOG.get(key)
    if info:
        return info
    # Fallback: capitalize the camelCase name
    name = normalized.replace("_", " ")
    import re
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name).title()
    return {"name": name, "name_en": name, "icon": None}


