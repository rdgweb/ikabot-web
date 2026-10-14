"""Texts of the public site (N-119), Portuguese and English side by side.

The long legal texts live in templates (website/legal/<page>_<lang>.html); everything else is here,
so a change of wording is one file and both languages stay in step.
"""

LANGS = ("pt", "en")
HTML_LANG = {"pt": "pt-BR", "en": "en"}
X_DEFAULT = "en"

PAGES = ("home", "pricing", "faq", "contact", "terms", "privacy", "refund")
SLUGS = {
    "pt": {"home": "", "pricing": "precos", "faq": "faq", "contact": "contato",
           "terms": "termos", "privacy": "privacidade", "refund": "reembolso"},
    "en": {"home": "", "pricing": "pricing", "faq": "faq", "contact": "contact",
           "terms": "terms", "privacy": "privacy", "refund": "refund"},
}
LEGAL_PAGES = ("terms", "privacy", "refund")


def path_of(lang: str, page: str) -> str:
    slug = SLUGS[lang][page]
    return f"/{lang}/{slug}/" if slug else f"/{lang}/"


META = {
    "pt": {
        "home": ("IkaHub — automação e painel para várias contas do Ikariam",
                 "Painel único para várias contas e mundos do Ikariam: construção, transporte, mercado entre as suas "
                 "contas e alertas no Telegram, com o agente rodando na sua máquina."),
        "pricing": ("Preços — IkaHub",
                    "Preço por subcontas ativas, a partir de US$ 4 por mês. Agente e proxy próprios incluídos; "
                    "agente e proxy da plataforma como adicionais."),
        "faq": ("Perguntas frequentes — IkaHub",
                "Subcontas, agente, proxies, segurança da senha, risco de banimento e cancelamento: as dúvidas mais comuns sobre o IkaHub."),
        "contact": ("Pedir convite — IkaHub", "O IkaHub está em beta fechado. Peça um convite e fale com a equipe."),
        "terms": ("Termos de uso — IkaHub", "Termos de uso e de serviço do IkaHub."),
        "privacy": ("Política de privacidade — IkaHub", "Quais dados o IkaHub guarda, para quê, por quanto tempo e como exercer seus direitos (LGPD)."),
        "refund": ("Cancelamento e reembolso — IkaHub", "Como cancelar o IkaHub e como funcionam os reembolsos."),
    },
    "en": {
        "home": ("IkaHub — automation and one dashboard for many Ikariam accounts",
                 "One dashboard for many Ikariam accounts and worlds: building, transport, trading between your own "
                 "accounts and Telegram alerts, with the agent running on your own machine."),
        "pricing": ("Pricing — IkaHub",
                    "Priced by active sub-accounts, from US$ 4 a month. Your own agent and proxies included; "
                    "platform agent and proxies as add-ons."),
        "faq": ("FAQ — IkaHub",
                "Sub-accounts, agent, proxies, password safety, ban risk and cancelling: the most common questions about IkaHub."),
        "contact": ("Request an invite — IkaHub", "IkaHub is in closed beta. Request an invite and talk to the team."),
        "terms": ("Terms of use — IkaHub", "IkaHub terms of use and service."),
        "privacy": ("Privacy policy — IkaHub", "What data IkaHub keeps, why, for how long and how to exercise your rights."),
        "refund": ("Cancellation and refunds — IkaHub", "How to cancel IkaHub and how refunds work."),
    },
}

UI = {
    "pt": {
        "nav": {"home": "Início", "pricing": "Preços", "faq": "FAQ", "contact": "Contato",
                "terms": "Termos", "privacy": "Privacidade", "refund": "Reembolso"},
        "login": "Entrar", "invite": "Pedir convite", "see_pricing": "Ver preços", "other_lang": "English",
        "beta": "Beta fechado · por convite",
        "footer_disclaimer": "O IkaHub não é afiliado, aprovado nem mantido pela Gameforge. Ikariam é marca registrada da Gameforge 4D GmbH.",
        "footer_legal": "Legal", "footer_product": "Produto",
        "updated": "Versão",
    },
    "en": {
        "nav": {"home": "Home", "pricing": "Pricing", "faq": "FAQ", "contact": "Contact",
                "terms": "Terms", "privacy": "Privacy", "refund": "Refunds"},
        "login": "Sign in", "invite": "Request an invite", "see_pricing": "See pricing", "other_lang": "Português",
        "beta": "Closed beta · invite only",
        "footer_disclaimer": "IkaHub is not affiliated with, endorsed or maintained by Gameforge. Ikariam is a registered trademark of Gameforge 4D GmbH.",
        "footer_legal": "Legal", "footer_product": "Product",
        "updated": "Version",
    },
}

HOME = {
    "pt": {
        "title": "Suas contas do Ikariam trabalhando sozinhas",
        "lead": "Um painel para todas as contas e mundos. Construção, transporte, mercado e avisos no Telegram — "
                "com o agente rodando na sua máquina, por um proxy para cada conta.",
        "steps_title": "Em três passos",
        "steps": [
            ("bi-hdd-network", "Instale o agente", "Um comando, em Linux ou Windows com Docker. O hub já entrega o comando com a chave do agente."),
            ("bi-person-badge", "Cadastre a conta", "A senha do GameForge fica cifrada e só vai para o seu agente."),
            ("bi-play-circle", "Escolha o que fazer", "Mundos, cidades e ações em fila. O painel mostra tudo acontecendo."),
        ],
        "features_title": "O que ele faz",
        "features": [
            ("bi-speedometer2", "Painel do jogo", "Cidades, recursos, tropas e alertas de todas as contas numa tela."),
            ("bi-list-task", "Ações em fila", "Transportes, construções e rotinas agendadas, com presets reutilizáveis."),
            ("bi-building", "Construção planejada", "Plano por cidade; o agente sobe os prédios quando há recurso."),
            ("bi-shop", "Mercado entre suas contas", "Recursos circulam entre as suas contas, sem misturar identidades."),
            ("bi-telegram", "Telegram", "Ataques, captchas e resumos chegam no seu chat; comandos respondem de lá."),
            ("bi-joystick", "Controle manual", "Abra o jogo pelo hub, na mesma sessão e no mesmo IP da conta."),
        ],
        "security_title": "Segurança primeiro",
        "security": [
            ("bi-lock", "Senha cifrada"),
            ("bi-key", "Chave por agente"),
            ("bi-shield-lock", "Agente só por HTTPS"),
            ("bi-people", "Cada cliente isolado"),
            ("bi-clock-history", "Registro de atividades"),
        ],
        "identity_title": "Uma conta, um agente, um proxy",
        "identity_text": "Contas no mesmo mundo saem por agentes e proxies diferentes. Contas do mesmo agente nunca "
                         "interagem entre si — o hub já respeita isso no mercado e no banco de generais.",
        "pricing_title": "Preço por subcontas ativas",
        "pricing_text": "A partir de US$ 4 por mês para até 4 subcontas, com agente e proxy próprios. Pausou uma subconta, liberou a vaga.",
        "cta_title": "Quer testar no beta?",
        "cta_text": "O IkaHub está em beta fechado. Peça um convite.",
        "mock": {"title": "Painel", "jobs": "Fila", "online": "agente online",
                 "rows": [("Transporte", "concluído"), ("Construção", "em andamento"), ("Mercado", "agendado")]},
    },
    "en": {
        "title": "Your Ikariam accounts, working on their own",
        "lead": "One dashboard for every account and world. Building, transport, trading and Telegram alerts — "
                "with the agent running on your own machine, through one proxy per account.",
        "steps_title": "Three steps",
        "steps": [
            ("bi-hdd-network", "Install the agent", "One command, on Linux or Windows with Docker. The hub hands you the command with the agent's key."),
            ("bi-person-badge", "Add the account", "The GameForge password is stored encrypted and only goes to your agent."),
            ("bi-play-circle", "Choose what to do", "Worlds, cities and queued actions. The dashboard shows it all happening."),
        ],
        "features_title": "What it does",
        "features": [
            ("bi-speedometer2", "Game dashboard", "Cities, resources, troops and alerts for every account on one screen."),
            ("bi-list-task", "Queued actions", "Transports, buildings and scheduled routines, with reusable presets."),
            ("bi-building", "Planned building", "A plan per city; the agent upgrades when the resources are there."),
            ("bi-shop", "Trading between your accounts", "Resources move between your own accounts without mixing identities."),
            ("bi-telegram", "Telegram", "Attacks, captchas and summaries reach your chat; commands answer from there."),
            ("bi-joystick", "Manual control", "Open the game through the hub, in the account's own session and IP."),
        ],
        "security_title": "Security first",
        "security": [
            ("bi-lock", "Encrypted password"),
            ("bi-key", "One key per agent"),
            ("bi-shield-lock", "Agent over HTTPS only"),
            ("bi-people", "Every customer isolated"),
            ("bi-clock-history", "Activity log"),
        ],
        "identity_title": "One account, one agent, one proxy",
        "identity_text": "Accounts in the same world go out through different agents and proxies. Accounts on the same "
                         "agent never interact — the hub already enforces it in trading and the generals bank.",
        "pricing_title": "Priced by active sub-accounts",
        "pricing_text": "From US$ 4 a month for up to 4 sub-accounts, with your own agent and proxies. Pause a sub-account and the slot is free.",
        "cta_title": "Want to try the beta?",
        "cta_text": "IkaHub is in closed beta. Request an invite.",
        "mock": {"title": "Dashboard", "jobs": "Queue", "online": "agent online",
                 "rows": [("Transport", "done"), ("Building", "running"), ("Trade", "scheduled")]},
    },
}

# Prices: the launch proposal (N-106). Monthly, in US dollars, by ACTIVE sub-accounts.
TIERS = [(1, 4, 4), (5, 10, 8), (11, 20, 14), (21, 40, 24), (41, 80, 40)]
ABOVE = {"from": 81, "each": "0,50"}
ADDONS = {"agent": 2, "proxy": 3}

PRICING = {
    "pt": {
        "title": "Preço por subcontas ativas",
        "lead": "Você paga pelas subcontas que estão trabalhando. Agente e proxies próprios já estão incluídos.",
        "note": "Valores previstos para o lançamento. Durante o beta fechado o acesso é por convite.",
        "per_month": "/mês", "subaccounts": "subcontas", "up_to": "até", "above": "Acima de 80",
        "above_text": "US$ 0,50 por subconta ativa",
        "included_title": "Incluído em todos",
        "included": ["Painel, ações e construção", "Mercado entre as suas contas", "Telegram por organização",
                     "Controle manual", "Registro de atividades", "Agente e proxies próprios"],
        "addons_title": "Adicionais",
        "addons_soon": "em breve",
        "addons": [("bi-hdd-network", "Agente da plataforma", "US$ 2/mês por conta GameForge",
                    "Rodamos o agente para você; um agente por conta, sempre com proxy."),
                   ("bi-globe2", "Proxies da plataforma", "US$ 3/mês por conta GameForge",
                    "Proxies reservados só para a sua conta, no login e no jogo.")],
        "trial_title": "Teste de 7 dias",
        "trial_text": "Quando a venda abrir, o teste cobre até 4 subcontas, sem cobrança nos primeiros 7 dias.",
        "what_title": "O que é uma subconta?",
        "what_text": "Cada conta de jogo, num mundo, dentro da sua conta GameForge. Pausada não conta.",
    },
    "en": {
        "title": "Priced by active sub-accounts",
        "lead": "You pay for the sub-accounts that are working. Your own agent and proxies are already included.",
        "note": "Planned launch prices. During the closed beta access is by invitation.",
        "per_month": "/mo", "subaccounts": "sub-accounts", "up_to": "up to", "above": "Above 80",
        "above_text": "US$ 0.50 per active sub-account",
        "included_title": "Included in every plan",
        "included": ["Dashboard, actions and building", "Trading between your accounts", "Telegram per organization",
                     "Manual control", "Activity log", "Your own agent and proxies"],
        "addons_title": "Add-ons",
        "addons_soon": "soon",
        "addons": [("bi-hdd-network", "Platform agent", "US$ 2/mo per GameForge account",
                    "We run the agent for you; one agent per account, always behind a proxy."),
                   ("bi-globe2", "Platform proxies", "US$ 3/mo per GameForge account",
                    "Proxies reserved for your account only, for login and gameplay.")],
        "trial_title": "7-day trial",
        "trial_text": "When sales open, the trial covers up to 4 sub-accounts, with no charge for the first 7 days.",
        "what_title": "What is a sub-account?",
        "what_text": "Each game account, in one world, inside your GameForge account. Paused ones don't count.",
    },
}

FAQ = {
    "pt": [
        ("O que é uma subconta?",
         "Cada conta de jogo num mundo, dentro da sua conta GameForge. Uma conta GameForge com 4 mundos são 4 subcontas. "
         "O preço conta só as ativas: pausar uma subconta libera a vaga."),
        ("Preciso deixar o computador ligado?",
         "O agente roda em qualquer máquina com Docker — PC, servidor ou VPS. Enquanto ele estiver ligado, as contas trabalham. "
         "O agente da plataforma, que roda por você, vem em breve."),
        ("Preciso de proxy?",
         "Para várias contas no mesmo mundo, sim: cada conta GameForge sai por um IP próprio, para não parecer multiconta. "
         "Você cadastra os seus proxies; os da plataforma vêm em breve."),
        ("Minha senha está segura?",
         "A senha do GameForge fica cifrada no banco e só é entregue ao seu agente, por HTTPS e com a chave daquele agente. "
         "Cada entrega fica no registro de atividades da sua organização."),
        ("Posso ser banido?",
         "Pode. Automatizar ações pode violar as regras do Ikariam, e a Gameforge pode advertir, suspender ou banir contas. "
         "Ao usar o IkaHub você assume esse risco."),
        ("O IkaHub é da Gameforge?",
         "Não. O IkaHub é independente e não tem vínculo com a Gameforge."),
        ("Em quais servidores funciona?",
         "Foi construído e testado em mundos do servidor brasileiro. Outros servidores podem funcionar, mas ainda não são oferecidos oficialmente."),
        ("Como funciona o teste de 7 dias?",
         "Quando a venda abrir, o teste cobre até 4 subcontas, sem cobrança nos primeiros 7 dias. Hoje o acesso é por convite."),
        ("Como cancelo?",
         "A qualquer momento. Durante o beta, peça para sair e apagamos seus dados. Os detalhes estão em Cancelamento e reembolso."),
    ],
    "en": [
        ("What is a sub-account?",
         "Each game account in one world, inside your GameForge account. One GameForge account in 4 worlds is 4 sub-accounts. "
         "Only active ones count: pausing a sub-account frees the slot."),
        ("Does my computer need to stay on?",
         "The agent runs on any machine with Docker — a PC, a server or a VPS. While it is on, your accounts work. "
         "The platform agent, which runs for you, is coming soon."),
        ("Do I need proxies?",
         "For several accounts in the same world, yes: each GameForge account goes out through its own IP, so it does not look like a multi-account. "
         "You add your own proxies; platform proxies are coming soon."),
        ("Is my password safe?",
         "The GameForge password is stored encrypted and is only handed to your agent, over HTTPS and with that agent's key. "
         "Every hand-over is written to your organization's activity log."),
        ("Can I get banned?",
         "Yes. Automating actions may break the Ikariam rules, and Gameforge may warn, suspend or ban accounts. "
         "By using IkaHub you take that risk."),
        ("Is IkaHub made by Gameforge?",
         "No. IkaHub is independent and has no connection with Gameforge."),
        ("Which servers does it work on?",
         "It was built and tested on worlds of the Brazilian server. Other servers may work but are not officially offered yet."),
        ("How does the 7-day trial work?",
         "When sales open, the trial covers up to 4 sub-accounts with no charge for the first 7 days. Today access is by invitation."),
        ("How do I cancel?",
         "Any time. During the beta, ask to leave and we delete your data. Details are in Cancellation and refunds."),
    ],
}

CONTACT = {
    "pt": {
        "title": "Pedir convite",
        "lead": "O IkaHub está em beta fechado. Conte um pouco sobre as suas contas e a equipe responde.",
        "email": "E-mail", "name": "Nome ou apelido no jogo", "accounts": "Quantas contas GameForge?",
        "accounts_choices": [("1", "1"), ("2-3", "2 a 3"), ("4+", "4 ou mais")],
        "message": "Mensagem (opcional)", "send": "Enviar pedido",
        "consent": "Ao enviar, você concorda com a {privacy}.", "privacy_link": "política de privacidade",
        "sent_title": "Pedido recebido", "sent_text": "Obrigado! Respondemos no e-mail informado.",
        "error_email": "Informe um e-mail válido.", "error_rate": "Muitos pedidos deste endereço. Tente de novo mais tarde.",
        "direct": "Fale direto",
    },
    "en": {
        "title": "Request an invite",
        "lead": "IkaHub is in closed beta. Tell us a little about your accounts and the team will reply.",
        "email": "Email", "name": "Name or in-game nickname", "accounts": "How many GameForge accounts?",
        "accounts_choices": [("1", "1"), ("2-3", "2 to 3"), ("4+", "4 or more")],
        "message": "Message (optional)", "send": "Send request",
        "consent": "By sending, you agree to the {privacy}.", "privacy_link": "privacy policy",
        "sent_title": "Request received", "sent_text": "Thank you! We will reply to the email you gave.",
        "error_email": "Enter a valid email.", "error_rate": "Too many requests from this address. Try again later.",
        "direct": "Talk to us",
    },
}
