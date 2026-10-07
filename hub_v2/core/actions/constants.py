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

# Menus = subjects (N-88). "construction" is also used as a behaviour switch by the job
# forms, so that key must not be renamed.
CAT_CONSTRUCTION = "construction"
CAT_RESOURCES = "resources"
CAT_RESEARCH = "research"
CAT_TEMPLE = "temple"
CAT_MILITARY = "military"
CAT_MARKET = "market"
CAT_MONITORING = "monitoring"
CAT_DIPLOMACY = "diplomacy"
CAT_ACCOUNT = "account"

# display order everywhere a list is grouped by menu
CATEGORY_ORDER = [
    CAT_CONSTRUCTION,
    CAT_RESOURCES,
    CAT_RESEARCH,
    CAT_TEMPLE,
    CAT_MILITARY,
    CAT_MARKET,
    CAT_MONITORING,
    CAT_DIPLOMACY,
    CAT_ACCOUNT,
]

CATEGORY_META = {
    CAT_CONSTRUCTION: {"label": "Construcao", "icon": "bi-building", "color": "var(--ik-sea)"},
    CAT_RESOURCES: {"label": "Recursos e logistica", "icon": "bi-box-seam", "color": "var(--ik-gold)"},
    CAT_RESEARCH: {"label": "Pesquisa", "icon": "bi-lightbulb", "color": "var(--ik-sea)"},
    CAT_TEMPLE: {"label": "Templo", "icon": "bi-stars", "color": "var(--ik-gold)"},
    CAT_MILITARY: {"label": "Militar", "icon": "bi-shield-fill", "color": "var(--ik-bad)"},
    CAT_MARKET: {"label": "Mercado", "icon": "bi-shop", "color": "var(--ik-warn)"},
    CAT_MONITORING: {"label": "Alertas e monitoramento", "icon": "bi-bell", "color": "var(--ik-sea)"},
    CAT_DIPLOMACY: {"label": "Diplomacia", "icon": "bi-envelope", "color": "var(--ik-good)"},
    CAT_ACCOUNT: {"label": "Conta", "icon": "bi-person-gear", "color": "var(--ik-muted)"},
}

# categories stored before N-88 (workflows, saved filters) -> where they went, when the
# action itself is not known
LEGACY_CATEGORY_MAP = {
    "economy": CAT_RESOURCES,
    "automation": CAT_RESOURCES,
    "admin": CAT_ACCOUNT,
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
