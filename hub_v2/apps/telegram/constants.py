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
        "label": "Job concluído",
        "description": "Notifica quando um job termina com sucesso.",
        "category": "operational",
        "icon": "bi-check-circle",
        "field": "notify_job_done",
    },
    {
        "key": "build_complete",
        "emitted": False,
        "label": "Construção concluída",
        "description": "Notifica quando uma construção de edifício termina.",
        "category": "operational",
        "icon": "bi-building",
        "field": "notify_build_complete",
    },
    {
        "key": "research_complete",
        "emitted": False,
        "label": "Pesquisa concluída",
        "description": "Notifica quando uma pesquisa termina.",
        "category": "operational",
        "icon": "bi-lightbulb",
        "field": "notify_research_complete",
    },
    {
        "key": "low_wine",
        "emitted": False,
        "label": "Vinho baixo",
        "description": "Notifica quando o estoque de vinho está baixo.",
        "category": "operational",
        "icon": "bi-cup-straw",
        "field": "notify_low_wine",
    },
    {
        "key": "daily_summary",
        "emitted": False,
        "label": "Resumo diário",
        "description": "Envia um resumo diário do status de todas as contas.",
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
        "label": "Cineteatro disponível",
        "description": "Avisa quando o cineteatro tem sessão disponível. A coleta e manual no jogo (o bônus exige assistir um vídeo de anuncio; não é automatizável).",
        "category": "operational",
        "icon": "bi-film",
        "field": "notify_cinema_available",
    },
    {
        "key": "raid_alert",
        "label": "Alvo rico",
        "description": "Cidade espionada com muitos recursos: estoque, defesa, frota do porto e a conta sugerida para o saque.",
        "category": "operational",
        "icon": "bi-gem",
        "field": "notify_raid_alert",
    },
    {
        "key": "combat_report",
        "label": "Relatório de combate",
        "description": "Envia cada round e o fim dos combates acompanhados pelo Monitor de Combate, com a imagem do campo de batalha.",
        "category": "operational",
        "icon": "bi-shield-shaded",
        "field": "notify_combat_report",
    },
    {
        "key": "proxy_change",
        "label": "Troca de proxy",
        "description": "Avisa quando a verificação automática troca o proxy de uma conta ou de um nó, libera um bloqueio de login causado por proxy, ou não acha proxy livre.",
        "category": "system",
        "icon": "bi-hdd-network",
        "field": "notify_proxy_change",
    },
]

# N-109: a toggle and a text exist for these, but no action of the hub or of the agent sends them yet
NOT_EMITTED = {e["key"] for e in EVENT_TYPES if e.get("emitted") is False}

# Lookup by field name -> event info
EVENT_BY_FIELD = {e["field"]: e for e in EVENT_TYPES}

# All toggle field names (for form generation)
TOGGLE_FIELDS = [e["field"] for e in EVENT_TYPES]
