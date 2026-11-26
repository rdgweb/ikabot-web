"""Product units and disclosures shared by the public pricing and sign-up pages."""
from apps.billing import plans
from apps.proxy.platform import PROXIES_PER_ACCOUNT


def offer_content(lang):
    agent, proxy = plans.brl(plans.ADDON_AGENT), plans.brl(plans.ADDON_PROXIES)
    if lang == 'en':
        return {
            'title': 'Plan + optional services: see the whole monthly cost',
            'intro': 'The plan covers active characters. Hosting and platform proxies are separate services charged per GameForge lobby.',
            'agent': f'{agent} per lobby/month', 'proxy': f'{proxy} per lobby/month ({PROXIES_PER_ACCOUNT} reserved proxies)',
            'agent_text': 'We host one agent for this lobby. It needs proxies, which may be your own or a separate platform package.',
            'proxy_text': f'A package of {PROXIES_PER_ACCOUNT} proxies reserved for one lobby, for login and gameplay. Agent hosting is separate.',
            'own': 'Your own agent and proxies have no IkaHub add-on fee. You provide the machine and proxies and pay any external provider costs.',
            'currency': 'Prices and estimates are in Brazilian reais (BRL). This page does not convert prices to USD.',
            'closed': 'Subscriptions are not open. These are reference prices, not an active payment request. Beta access follows your invitation and any trial or courtesy granted.',
            'open': 'Monthly subscription. A trial or courtesy can cover services temporarily; review the dates in your organization before paying.',
            'trial': 'The standard trial lasts 7 days for up to 4 active characters, without a card. Platform services depend on available capacity and the trial or courtesy granted; no automatic paid subscription is created by signing up.',
            'glossary': [('GameForge account / lobby', 'One login that can hold several characters in different game worlds.'),
                         ('Character / sub-account', 'One character in one world. Active characters determine the plan tier.'),
                         ('Node and agent', 'The node identifies where jobs run; the agent is the program connected to that node. An organization is the workspace, not a lobby.')],
            'simulate': 'Estimate your monthly cost', 'calculate': 'Calculate estimate', 'base': 'Plan',
            'agents': 'Platform agents', 'proxies': 'Platform proxy packages', 'total': 'Estimated recurring total',
            'per_month': '/month', 'up_to': 'up to', 'characters': 'active characters',
            'estimate_note': 'This simulation does not reserve capacity, enable services or create a charge. Add-ons may remain billable while reserved, even if characters are paused. Initial payments and prorated differences are shown separately at checkout.',
            'pricing_link': 'See prices and estimate your total before choosing',
        }
    return {
        'title': 'Plano + serviços opcionais: veja o custo mensal completo',
        'intro': 'O plano cobre personagens ativos. Hospedagem do agente e proxies da plataforma são serviços separados, cobrados por lobby GameForge.',
        'agent': f'{agent} por lobby/mês', 'proxy': f'{proxy} por lobby/mês ({PROXIES_PER_ACCOUNT} proxies reservados)',
        'agent_text': 'Hospedamos um agente para este lobby. Ele precisa de proxies, que podem ser seus ou de um pacote separado da plataforma.',
        'proxy_text': f'Pacote de {PROXIES_PER_ACCOUNT} proxies reservados para um lobby, no login e no jogo. A hospedagem do agente é separada.',
        'own': 'Agente e proxies próprios não geram adicional no IkaHub. Você fornece a máquina e os proxies e arca com eventuais custos dos seus provedores.',
        'currency': 'Preços e estimativas em reais (BRL).',
        'closed': 'As assinaturas ainda não estão abertas. Estes são preços de referência, não um pedido de pagamento. O acesso no beta segue o convite e o teste ou cortesia liberados.',
        'open': 'Assinatura mensal. Teste ou cortesia podem cobrir serviços temporariamente; confira os prazos na sua organização antes de pagar.',
        'trial': 'O teste padrão dura 7 dias para até 4 personagens ativos, sem cartão. Os serviços da plataforma dependem de vagas e do teste ou cortesia liberados; cadastrar-se não cria assinatura paga automaticamente.',
        'glossary': [('Conta GameForge / lobby', 'Um login que pode reunir vários personagens em mundos diferentes.'),
                     ('Personagem / subconta', 'Um personagem em um mundo. Os personagens ativos definem a faixa do plano.'),
                     ('Nó e agente', 'O nó identifica onde os jobs rodam; o agente é o programa conectado a ele. Organização é o espaço de trabalho, não um lobby.')],
        'simulate': 'Simule seu custo mensal', 'calculate': 'Calcular estimativa', 'base': 'Plano',
        'agents': 'Agentes da plataforma', 'proxies': 'Pacotes de proxies da plataforma', 'total': 'Total mensal recorrente estimado',
        'per_month': '/mês', 'up_to': 'até', 'characters': 'personagens ativos',
        'estimate_note': 'A simulação não reserva vagas, ativa recursos ou gera cobrança. Adicionais podem continuar cobrados enquanto reservados, mesmo com personagens pausados. Pagamento inicial e diferenças proporcionais aparecem separados na contratação.',
        'pricing_link': 'Veja os preços e simule o total antes de escolher',
    }
