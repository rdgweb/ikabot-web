from .constants import CAT_ADMIN


ACTIONS = {
    100: {
        "name": "Verificar Status",
        "name_en": "Check account status",
        "category": CAT_ADMIN,
        "icon": "bi-clipboard-check",
        "runner": "check_status",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "ready": True,
        "description": "Coleta dados atuais da conta: recursos, edificios, tropas.",
        "inputs": [],
    },
    101: {
        "name": "Descobrir Personagens",
        "name_en": "Discover characters",
        "category": CAT_ADMIN,
        "icon": "bi-search",
        "runner": "discover_characters",
        "requires_game_session": False,
        "recurring": False,
        "long_running": False,
        "ready": True,
        "description": "Descobre subcontas/servidores do lobby Gameforge.",
        "inputs": [],
    },
    4: {
        "name": "Status da Conta",
        "name_en": "Account status",
        "category": CAT_ADMIN,
        "icon": "bi-info-circle",
        "runner": "status",
        "requires_game_session": True,
        "recurring": False,
        "long_running": False,
        "description": "Mostra status resumido da conta.",
        "inputs": [],
    },
}
