FIELD_INT = "int"
FIELD_STR = "str"
FIELD_BOOL = "bool"
FIELD_CHOICE = "choice"
FIELD_CITY_SELECT = "city_select"
FIELD_RESOURCE_TYPE = "resource_type"
FIELD_DONATION_TYPE = "donation_type"
FIELD_BUILDING_SELECT = "building_select"
FIELD_UNIT_SELECT = "unit_select"
FIELD_JSON_ARRAY = "json_array"

CAT_ADMIN = "admin"
CAT_ECONOMY = "economy"
CAT_CONSTRUCTION = "construction"
CAT_MILITARY = "military"
CAT_MARKET = "market"
CAT_MONITORING = "monitoring"
CAT_AUTOMATION = "automation"

CATEGORY_META = {
    CAT_ADMIN: {"label": "Administracao", "icon": "bi-gear", "color": "var(--ik-muted)"},
    CAT_ECONOMY: {"label": "Economia", "icon": "bi-coin", "color": "var(--ik-gold)"},
    CAT_CONSTRUCTION: {"label": "Construcao", "icon": "bi-building", "color": "var(--ik-sea)"},
    CAT_MILITARY: {"label": "Militar", "icon": "bi-shield-fill", "color": "var(--ik-bad)"},
    CAT_MARKET: {"label": "Mercado", "icon": "bi-shop", "color": "var(--ik-warn)"},
    CAT_MONITORING: {"label": "Monitoramento", "icon": "bi-bell", "color": "var(--ik-sea)"},
    CAT_AUTOMATION: {"label": "Automacao", "icon": "bi-arrow-repeat", "color": "var(--ik-good)"},
}

RESOURCE_CHOICES = [
    (0, "Madeira", "game/resources/icon_wood.png"),
    (1, "Vinho", "game/resources/icon_wine.png"),
    (2, "Marmore", "game/resources/icon_marble.png"),
    (3, "Cristal", "game/resources/icon_glass.png"),
    (4, "Enxofre", "game/resources/icon_sulfur.png"),
]

DONATION_CHOICES = [
    ("wood", "Madeira (floresta)", "game/resources/icon_wood.png"),
    ("tradegood", "Bem comercial (ilha)", "game/resources/icon_wine.png"),
]

DONATION_METHOD_CHOICES = [
    ("1", "Excedente do armazem"),
    ("2", "% da producao no intervalo"),
    ("3", "Quantidade fixa"),
]

RESEARCH_REDUCTION_CHOICES = [
    ("0", "0% (nada pesquisado)"),
    ("2", "2% (Flaschenzug)"),
    ("6", "6% (Geometria)"),
    ("14", "14% (Wasserwaage)"),
]

CONSTRUCTION_TIME_CHOICES = [
    ("0", "0%"),
    ("10", "-10%"),
    ("20", "-20%"),
    ("25", "-25%"),
    ("30", "-30%"),
    ("50", "-50%"),
    (":3", "Ares (:3)"),
]

TRANSPORT_LOAD_CHOICES = [
    ("100", "100% (max carga)"),
    ("80", "80%"),
    ("60", "60%"),
    ("40", "40%"),
    ("20", "20% (mais rapido)"),
]

DISTRIBUTION_STRATEGY_CHOICES = [
    ("targets", "Manter minimos e alvos"),
    ("producer_to_non_producer", "Produtor -> nao produtor"),
    ("evenly", "Equilibrar estoques"),
]
