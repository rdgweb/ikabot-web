from .constants import (
    CAT_MARKET,
    FIELD_CITY_SELECT,
    FIELD_INT,
    FIELD_RESOURCE_TYPE,
    FIELD_STR,
)


ACTIONS = {
    # Manual market actions (user-created via UI)
    8: {
        "name": "Comprar do Mercado",
        "name_en": "Buy from Market",
        "category": CAT_MARKET,
        "icon": "bi-cart-plus",
        "runner": "buy_market",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "ready": True,
        "description": "Compra recursos de um vendedor especifico no Branch Office.",
        "inputs": [
            {"key": "buyer_city_id", "type": FIELD_CITY_SELECT, "label": "Cidade Compradora", "required": True},
            {"key": "seller_city_id", "type": FIELD_STR, "label": "Cidade Vendedora (ID)", "required": True, "placeholder": "ID da cidade do vendedor"},
            {"key": "resource_idx", "type": FIELD_RESOURCE_TYPE, "label": "Recurso", "required": True},
            {"key": "amount", "type": FIELD_INT, "label": "Quantidade", "required": True, "min": 1},
        ],
    },
    9: {
        "name": "Vender no Mercado",
        "name_en": "Sell on Market",
        "category": CAT_MARKET,
        "icon": "bi-cart-dash",
        "runner": "sell_market",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "ready": True,
        "description": "Cria uma oferta de venda no Branch Office da conta.",
        "inputs": [
            {"key": "city_id", "type": FIELD_CITY_SELECT, "label": "Cidade", "required": True},
            {"key": "resource_idx", "type": FIELD_RESOURCE_TYPE, "label": "Recurso", "required": True},
            {"key": "amount", "type": FIELD_INT, "label": "Quantidade", "required": True, "min": 1},
            {"key": "unit_price", "type": FIELD_INT, "label": "Preco por unidade", "required": True, "min": 1},
        ],
    },

    # Internal market runners (created automatically by hub - hidden from UI)
    801: {
        "name": "Compra Interna (auto)",
        "name_en": "Internal Buy (auto)",
        "category": CAT_MARKET,
        "icon": "bi-cart-plus",
        "runner": "buy_resources",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "ui_hidden": True,
        "description": "Criado automaticamente pelo hub para completar uma ordem interna de compra.",
        "inputs": [],
    },
    802: {
        "name": "Venda Interna (auto)",
        "name_en": "Internal Sell (auto)",
        "category": CAT_MARKET,
        "icon": "bi-cart-dash",
        "runner": "sell_resources",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "ui_hidden": True,
        "description": "Criado automaticamente pelo hub para completar uma ordem interna de venda.",
        "inputs": [],
    },
}
