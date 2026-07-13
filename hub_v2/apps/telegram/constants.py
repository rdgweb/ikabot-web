"""
Constantes do app Telegram — registro de tipos de evento e labels PT-BR.
"""

EVENT_TYPES = [
    {
        "key": "attack_alert",
        "label": "Alerta de ataque",
        "description": "Notifica quando uma cidade recebe ataque.",
        "category": "operational",
        "icon": "bi-exclamation-triangle-fill",
        "field": "notify_attack_alert",
    },
    {
        "key": "job_failed",
        "label": "Job falhou",
        "description": "Notifica quando um job termina com erro.",
        "category": "operational",
        "icon": "bi-x-octagon",
        "field": "notify_job_failed",
    },
    {
        "key": "job_done",
        "label": "Job concluido",
        "description": "Notifica quando um job termina com sucesso.",
        "category": "operational",
        "icon": "bi-check-circle",
        "field": "notify_job_done",
    },
    {
        "key": "build_complete",
        "label": "Construcao concluida",
        "description": "Notifica quando uma construcao de edificio termina.",
        "category": "operational",
        "icon": "bi-building",
        "field": "notify_build_complete",
    },
    {
        "key": "research_complete",
        "label": "Pesquisa concluida",
        "description": "Notifica quando uma pesquisa termina.",
        "category": "operational",
        "icon": "bi-lightbulb",
        "field": "notify_research_complete",
    },
    {
        "key": "low_wine",
        "label": "Vinho baixo",
        "description": "Notifica quando o estoque de vinho esta baixo.",
        "category": "operational",
        "icon": "bi-cup-straw",
        "field": "notify_low_wine",
    },
    {
        "key": "daily_summary",
        "label": "Resumo diario",
        "description": "Envia um resumo diario do status de todas as contas.",
        "category": "system",
        "icon": "bi-clipboard-data",
        "field": "notify_daily_summary",
    },
    {
        "key": "diplomacy_message",
        "label": "Mensagem de diplomacia",
        "description": "Notifica novas mensagens de diplomacia capturadas pelo agente.",
        "category": "operational",
        "icon": "bi-envelope",
        "field": "notify_diplomacy_message",
    },
    {
        "key": "island_monitor",
        "label": "Monitor de ilha",
        "description": "Notifica quando cidades aparecem, somem ou trocam de jogador em ilhas monitoradas.",
        "category": "operational",
        "icon": "bi-geo-alt-fill",
        "field": "notify_island_monitor",
    },
    {
        "key": "cinema_available",
        "label": "Cineteatro disponivel",
        "description": "Avisa quando o cineteatro tem sessao disponivel. A coleta e manual no jogo (o bonus exige assistir um video de anuncio; nao e automatizavel).",
        "category": "operational",
        "icon": "bi-film",
        "field": "notify_cinema_available",
    },
]

# Lookup by field name -> event info
EVENT_BY_FIELD = {e["field"]: e for e in EVENT_TYPES}

# All toggle field names (for form generation)
TOGGLE_FIELDS = [e["field"] for e in EVENT_TYPES]
