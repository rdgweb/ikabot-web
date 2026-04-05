from .constants import CAT_MARKET, FIELD_CITY_SELECT, FIELD_INT, FIELD_RESOURCE_TYPE


ACTIONS = {
    801: {
        "name": "Comprar Recursos",
        "name_en": "Buy resources",
        "category": CAT_MARKET,
        "icon": "bi-cart-plus",
        "runner": "buy_resources",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "description": "Compra recursos no mercado.",
        "inputs": [
            {"key": "city", "type": FIELD_CITY_SELECT, "label": "Cidade", "multiple": False, "required": True},
            {"key": "resource_type", "type": FIELD_RESOURCE_TYPE, "label": "Recurso", "required": True},
            {"key": "amount", "type": FIELD_INT, "label": "Quantidade", "required": True, "min": 1},
        ],
    },
    802: {
        "name": "Vender Recursos",
        "name_en": "Sell resources",
        "category": CAT_MARKET,
        "icon": "bi-cart-dash",
        "runner": "sell_resources",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "description": "Vende recursos no mercado.",
        "inputs": [
            {"key": "city", "type": FIELD_CITY_SELECT, "label": "Cidade", "multiple": False, "required": True},
            {"key": "resource_type", "type": FIELD_RESOURCE_TYPE, "label": "Recurso", "required": True},
            {"key": "amount", "type": FIELD_INT, "label": "Quantidade", "required": True, "min": 1},
            {"key": "price", "type": FIELD_INT, "label": "Preco por unidade", "required": False, "min": 1, "help": "Vazio = preco do mercado."},
        ],
    },
}
