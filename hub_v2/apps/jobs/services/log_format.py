"""Apresentacao dos logs de execucao (N-91).

Os agentes gravam cada linha como texto corrido, quase sempre assim:

    Postando doacao: planejado=9,264 | efetivo=9,264 | faltava_antes=988,692
    Rescheduled in 87286s
    Login: s78-br | rdg*** | proxy 1/3

Aqui a linha vira algo que se le: um titulo, campos com nome e valor, duracoes em
horas e minutos, cidades pelo nome, recursos com o icone, e um icone que diz do que
se trata. Tudo acontece na hora de exibir, entao vale para todas as acoes e tambem
para o historico ja gravado, sem depender de mudar cada runner.

A mensagem original nunca e alterada no banco e fica disponivel em `raw`.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

LEVELS = {
    "error": {"label": "Erro", "icon": "bi-x-circle-fill", "color": "var(--ik-bad)", "rank": 3},
    "warn": {"label": "Aviso", "icon": "bi-exclamation-triangle-fill", "color": "#d97706", "rank": 2},
    "info": {"label": "Info", "icon": "bi-info-circle-fill", "color": "var(--ik-sea)", "rank": 1},
    "debug": {"label": "Debug", "icon": "bi-bug", "color": "var(--ik-muted)", "rank": 0},
}
LEVEL_ORDER = ("error", "warn", "info", "debug")

RESOURCES = {
    "wood": ("Madeira", "game/resources/icon_wood.png"),
    "madeira": ("Madeira", "game/resources/icon_wood.png"),
    "wine": ("Vinho", "game/resources/icon_wine.png"),
    "vinho": ("Vinho", "game/resources/icon_wine.png"),
    "marble": ("Mármore", "game/resources/icon_marble.png"),
    "marmore": ("Mármore", "game/resources/icon_marble.png"),
    "mármore": ("Mármore", "game/resources/icon_marble.png"),
    "crystal": ("Cristal", "game/resources/icon_glass.png"),
    "glass": ("Cristal", "game/resources/icon_glass.png"),
    "cristal": ("Cristal", "game/resources/icon_glass.png"),
    "vidro": ("Cristal", "game/resources/icon_glass.png"),
    "sulfur": ("Enxofre", "game/resources/icon_sulfur.png"),
    "enxofre": ("Enxofre", "game/resources/icon_sulfur.png"),
    "gold": ("Ouro", "game/resources/icon_gold.png"),
    "ouro": ("Ouro", "game/resources/icon_gold.png"),
}

# nome de campo gravado pelo agente -> como mostrar
KEY_LABELS = {
    "destino": "Destino", "origem": "Origem", "metodo": "Método", "valor": "Valor", "motivo": "Motivo",
    "carry_over": "Sobra acumulada", "intervalo": "Intervalo", "jitter_max": "Variação máx.",
    "planejado": "Planejado", "efetivo": "Efetivo", "faltava_antes": "Faltava antes", "sobra_proximo": "Sobra p/ o próximo",
    "doado": "Doado", "falta_agora": "Falta agora", "proximo_em": "Próximo em", "proxima": "Próxima",
    "fila": "Fila", "carregamento": "Carregamento", "viagem": "Viagem", "total": "Total", "ida": "Ida",
    "queue": "Fila", "loading": "Carregamento", "travel": "Viagem", "check_em": "Conferir em",
    "solicitado": "Solicitado", "despachavel": "Despachável", "despachado": "Despachado", "restante": "Restante",
    "navios_livres": "Navios livres", "capacidade/navio": "Capacidade por navio", "capacidade": "Capacidade",
    "movimentos": "Movimentos", "hostis": "Hostis", "alertaveis": "Alertáveis", "novos": "Novos",
    "cidade": "Cidade", "cidades": "Cidades", "city_id": "Cidade", "cidade_id": "Cidade", "id": "Cidade",
    "target_city": "Cidade alvo", "seller_city_id": "Cidade vendedora", "buyer_city_id": "Cidade compradora",
    "estoque_total": "Estoque total", "necessidade_total": "Necessidade total", "disponivel_total": "Disponível total",
    "favor": "Favor", "tarefas": "Tarefas", "coletadas": "Coletadas", "pendentes": "Pendentes", "coletaveis": "Coletáveis",
    "modo": "Modo", "entregue_detectado": "Entregue", "minimo": "Mínimo", "maximo": "Máximo",
    "job": "Job", "job_id": "Job", "child_job": "Job filho", "order": "Ordem", "order_id": "Ordem", "proxy": "Proxy",
    "preco": "Preço", "limites": "Limites", "pedido": "Pedido",
    "bo": "Mercado (posição)", "ativo": "Ativo", "livre": "Livre", "modal": "Navio", "status": "Status",
    "nivel": "Nível", "posicao": "Posição", "pos": "Posição", "edificio": "Edifício", "predio": "Prédio",
    "building_id": "Edifício (id)", "recurso": "Recurso", "quantidade": "Quantidade",
    "tentativa": "Tentativa", "erro": "Erro", "pagina": "Página", "alcance": "Alcance", "eta": "Chegada em",
    "felicidade": "Felicidade", "felicidade_total": "Felicidade total", "taverna": "Taberna", "crescimento": "Crescimento",
    "cobertura": "Cobertura", "cobertura_atual": "Cobertura atual", "cobertura_alvo": "Cobertura alvo",
    "estoque": "Estoque", "estoque_real": "Estoque real", "net": "Saldo líquido", "necessidade_bruta": "Necessidade bruta",
    "planejado_cadeia": "Planejado na cadeia", "pendente": "Pendente", "estimado": "Estimado", "custo_real": "Custo real",
    "faltando": "Faltando", "falta": "Falta", "botao_upgrade": "Botão de ampliar", "fase": "Fase", "detalhe": "Detalhe",
    "returning": "Voltando", "incoming_eta": "Chegada prevista", "delta": "Diferença", "host": "Servidor", "port": "Porta",
    "saldo": "Saldo", "gastavel": "Gastável", "max_unidades": "Máx. de unidades", "timeout": "Tempo limite",
    "mission": "Missão", "retry_em": "Nova tentativa em", "countdown": "Contagem", "header": "Cabeçalho",
    "evidencia": "Evidência", "total_ate_compra": "Total até a compra", "player_id": "Jogador", "serraria": "Serraria",
    "luxo": "Bem de luxo", "cidadaos": "Cidadãos", "snapshot": "Snapshot", "servico": "Serviço",
    "favor_restante": "Favor restante", "deuses": "Deuses", "cidades_extra": "Cidades extras", "fleet": "Frota",
    "carga": "Carga", "wait": "Espera", "fallback": "Alternativa",
}

CITY_KEYS = (
    "cidade", "city_id", "cidade_id", "id", "from_city", "to_city", "target_city", "seller_city_id", "buyer_city_id",
    "origem", "destino", "city",
)
JOB_KEYS = ("job", "job_id", "child_job", "job_filho", "parent_job", "source_job", "monitor_job", "followup_job")
ORDER_KEYS = ("order", "order_id", "ordem", "ordem_id")
# numeros que sao identificadores, nao quantidades: ficam como estao
ID_KEYS = (*CITY_KEYS, *JOB_KEYS, *ORDER_KEYS, "player_id", "building_id", "bo", "pos", "posicao", "port", "ilha", "island", "tentativa", "pagina")

# codigos internos que aparecem como valor
VALUE_LABELS = {
    "exact_stock": "estoque exato", "trade_advisor": "consultor de comércio", "jobs_running": "jobs em execução",
    "gods_not_researched": "deuses ainda não pesquisados", "port_blocked": "porto bloqueado", "ports_blocked": "portos bloqueados",
    "arrival_check": "conferência de chegada", "market_min_gold": "ouro mínimo do mercado",
    "stock_arrived": "estoque chegou",
}

# palavras que os agentes gravam sem acento e que so tem uma leitura
ACCENTS = {
    "doacao": "doação", "doacoes": "doações", "concluido": "concluído", "concluida": "concluída",
    "proximo": "próximo", "proxima": "próxima", "proximos": "próximos", "proximas": "próximas",
    "sessao": "sessão", "nao": "não", "ja": "já", "ate": "até", "apos": "após", "sera": "será", "estao": "estão", "sao": "são",
    "diario": "diário", "diaria": "diária", "diarias": "diárias", "bonus": "bônus", "minimo": "mínimo", "maximo": "máximo",
    "nivel": "nível", "niveis": "níveis", "acao": "ação", "acoes": "ações",
    "armazem": "armazém", "codigo": "código", "numero": "número", "unico": "único", "ultimo": "último", "ultima": "última",
    "cidadaos": "cidadãos", "cidadao": "cidadão", "edificio": "edifício", "edificios": "edifícios", "predio": "prédio",
    "ferias": "férias", "trafego": "tráfego", "inicio": "início", "termino": "término", "periodo": "período",
    "historico": "histórico", "automatico": "automático", "automatica": "automática", "valido": "válido", "valida": "válida",
    "invalido": "inválido", "invalida": "inválida", "necessario": "necessário", "necessaria": "necessária",
    "temporario": "temporário", "temporaria": "temporária", "transitorio": "transitório", "obrigatorio": "obrigatório",
    "obrigatoria": "obrigatória", "obrigatorios": "obrigatórios", "usuario": "usuário", "saida": "saída",
    "pagina": "página", "paginas": "páginas", "tambem": "também", "alem": "além", "porem": "porém", "atras": "atrás",
    "so": "só", "ha": "há", "voce": "você", "util": "útil", "uteis": "úteis", "santuario": "santuário",
    "espiao": "espião", "espioes": "espiões", "missao": "missão", "missoes": "missões", "marmore": "mármore",
    "balcao": "balcão", "transito": "trânsito", "rapido": "rápido", "rapida": "rápida", "lancamento": "lançamento",
    "orcamento": "orçamento", "cabecalho": "cabeçalho", "preco": "preço", "precos": "preços", "forca": "força",
    "forcas": "forças", "comeca": "começa", "comecar": "começar", "alcancado": "alcançado", "alcancada": "alcançada",
    "barbaros": "bárbaros", "colonia": "colônia", "colonias": "colônias", "botao": "botão", "servico": "serviço",
    "horario": "horário", "calculo": "cálculo", "ambrosia": "ambrósia", "pirotecnico": "pirotécnico",
    "nautica": "náutica", "nauticas": "náuticas", "tecnico": "técnico", "tecnica": "técnica", "basico": "básico",
    "proprias": "próprias", "proprios": "próprios", "propria": "própria", "proprio": "próprio", "multiplo": "múltiplo",
}
_ACCENT_RE = re.compile(r"(?<![\w=/])(" + "|".join(sorted(map(re.escape, ACCENTS), key=len, reverse=True)) + r")(?![\w=/])", re.IGNORECASE)
# terminacoes que em portugues sempre levam acento ("distribuicao", "disponivel", "evidencia")
_SUFFIXES = {
    "coes": "ções", "cao": "ção", "sao": "são", "aveis": "áveis", "iveis": "íveis", "avel": "ável", "ivel": "ível",
    "encia": "ência", "ancia": "ância",
}
_SUFFIX_RE = re.compile(r"(?<![\w=])([A-Za-z]{2,}?)(coes|cao|sao|aveis|iveis|avel|ivel|encia|ancia)(?![\w=/])")
_SUFFIX_SKIP = {"travel", "gravel", "marvel", "level", "swivel"}
# "esta" sem acento so e corrigido onde e claramente o verbo ("a oferta esta a 21", "esta bloqueado")
_VERB_ESTA = re.compile(
    r"\b([Ee]sta)( (?:a \d|acima|abaixo|bloquead|ocupad|vazi|chei|indispon|dispon|em |sem |com |fora|ativ|inativ|pront|pausad|offline|online))"
)

# titulos frequentes que so quem escreveu o runner entende (comeco da frase, em minusculas)
TITLE_REWRITES = (
    ("eta transporte", "Tempo estimado do transporte"),
    ("advisor militar", "Movimentos militares"),
    ("monitor de chegada agendado", "Conferência de chegada agendada"),
    ("inbox:", "Caixa de entrada:"),
    ("verificando inbox", "Verificando a caixa de entrada"),
)

_FAILED = {
    "Daily login": "Login diário falhou", "Login": "Login falhou", "Collect resources": "Coleta de recursos falhou",
    "Build museum": "Organização do museu falhou", "Build": "Construção falhou", "Upgrade": "Ampliação falhou",
    "Sell offer": "Oferta de venda falhou", "Sell": "Venda falhou", "Buy premium": "Compra premium falhou",
    "Buy": "Compra falhou", "Attack": "Ataque falhou", "Colonize": "Colonização falhou",
    "Abandon colony": "Abandono da colônia falhou", "Activate miracle": "Ativação do milagre falhou",
    "Activate Ambrosia": "Ativação da Ambrósia falhou", "Scan island": "Leitura da ilha falhou",
    "Scan player": "Consulta do jogador falhou", "Setup trade route": "Configuração da rota comercial falhou",
}
# frases que os agentes gravam em ingles, e erros tecnicos de rede: (regex, como dizer)
PHRASES = tuple((re.compile(pattern), replacement) for pattern, replacement in (
    (r"^Starting login for account \S+$", "Iniciando login"),
    (r"^Login successful$", "Login concluído"),
    (r"^Collecting resources for account \S+$", "Coletando recursos"),
    (r"^Launching attack for account \S+$", "Lançando ataque"),
    (r"^Activating miracle for account \S+$", "Ativando milagre"),
    (r"^Scanning island for account \S+$", "Lendo a ilha"),
    (r"^Scanning player for account \S+$", "Consultando jogador"),
    (r"^Buying premium feature for account \S+$", "Comprando recurso premium"),
    (r"^Activating Ambrosia bonus for account \S+$", "Ativando bônus de Ambrósia"),
    (r"^Setting up trade route for account \S+$", "Configurando rota comercial"),
    (r"^Arranging museum for account \S+$", "Organizando o museu"),
    (r"^Attack launched$", "Ataque lançado"),
    (r"^Miracle activated$", "Milagre ativado"),
    (r"^Island scan complete$", "Leitura da ilha concluída"),
    (r"^Player scan complete$", "Consulta do jogador concluída"),
    (r"^Premium purchase complete$", "Compra premium concluída"),
    (r"^Ambrosia bonus activated$", "Bônus de Ambrósia ativado"),
    (r"^Trade route configured$", "Rota comercial configurada"),
    (r"^Museum arranged$", "Museu organizado"),
    (r"^Sell offer created$", "Oferta de venda criada"),
    (r"^Creating sell offer: ", "Criando oferta de venda: "),
    (r"^Fetching homepage\.\.\.$", "Buscando a página inicial..."),
    (r"^Fetching city (\d+)\.\.\.$", r"Buscando cidade \1..."),
    (r"^Missing required inputs(?: for \w+)?", "Faltam parâmetros obrigatórios"),
    (r"\bOrder marked as completed\b", "Ordem marcada como concluída"),
    (r"\bOffer not found in listing\b", "Oferta não encontrada na listagem"),
    (r"^Daily tasks\b", "Tarefas diárias"),
    (r"^Retry manual solicitado\b", "Nova tentativa manual solicitada"),
    (r"\bGuard de ouro\b", "Reserva de ouro"),
    (r"\b(" + "|".join(sorted(map(re.escape, _FAILED), key=len, reverse=True)) + r") failed: ", lambda match: _FAILED[match.group(1)] + ": "),
    (r"HTTPS?ConnectionPool\(host='([^']+)', port=\d+\): Read timed out\. \(read timeout=([\d.]+)\)", r"sem resposta de \1 em \2s"),
    (r"HTTPS?ConnectionPool\(host='([^']+)', port=\d+\): Max retries exceeded with url: \S+ \(Caused by .*\)", r"não foi possível conectar a \1"),
    (r"\bRead timed out\b\.?", "tempo de resposta esgotado"),
    (r"\bConnection refused\b", "conexão recusada"),
    (r"\bConnection (?:reset by peer|aborted)\b\.?", "conexão interrompida"),
    (r"\bplayer_id=(\d+)", r"jogador \1"),
))

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(_UUID)
_KEY = r"[A-Za-z_][\w/]{0,29}(?: [a-z_]\w{0,19})?"
_KV = re.compile(rf"^({_KEY})=(.*)$", re.S)
_HEAD_WITH_KV = re.compile(rf"^(?P<title>[^=|]*?)\s*[:\-]\s*(?P<kv>{_KEY}=.*)$", re.S)
_HEAD_WITH_PARENS = re.compile(r"^(?P<title>.*?)\s*\((?P<kv>[^()]*\w=[^()]*)\)\.?$", re.S)
_JOB_AT_END = re.compile(rf"^(.*\bjob\b[^=|]*?):? ({_UUID})\.?$", re.IGNORECASE | re.S)
_NESTED_KV = re.compile(r"^([A-Za-zÀ-ÿ_]{2,20})=(\S.*)$")
_NAMED_ITEM = re.compile(r"^([^()=]{1,40}?) \(([^()]*)\)$")
_TAG = re.compile(r"^\[([^\]]{1,60})\]\s*")
_SECONDS = re.compile(r"^(\d+(?:\.\d+)?)\s*s$")
_MINUTES = re.compile(r"^(\d+)\s*min$")
_ENGLISH_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+)(?![\w,]*\d)")
_BARE_NUMBER = re.compile(r"(?<![\w.,])(\d{5,})(?![\w.,])")
_INLINE_SECONDS = re.compile(r"(?<![\w.,])(\d{2,7})s\b")
_CODE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
_RESOURCE_AMOUNT = re.compile(r"^(madeira|vinho|m[aá]rmore|cristal|enxofre|wood|wine|marble|crystal|glass|sulfur)\s+x\s?([\d.,]+)$", re.IGNORECASE)

# o que a linha conta, para escolher o icone (o primeiro que casar): (regex, icone, familia, imagem do jogo)
FAMILIES = (
    (r"^job started|execu[cç][aã]o iniciada", "bi-play-circle-fill", "start", ""),
    (r"^rescheduled|reagend|pr[oó]xim[ao] (avalia|check|ciclo)|nova tentativa", "bi-clock-history", "wait", "game/resources/icon_time.png"),
    (r"\blogin\b|sess[aã]o|credenc|blackbox|captcha|cooldown", "bi-person-check", "session", ""),
    (r"proxy", "bi-hdd-network", "session", ""),
    (r"transporte|mercante|cargueiro|navio|barco|remessa|despach|chegada|\brotas?\b", "bi-truck", "transport", "game/units/barco_mercante.png"),
    (r"doa[cç][aã]o|doado|floresta|serraria", "bi-tree", "donation", "game/buildings/forestershouse.png"),
    (r"planejamento|distribui", "bi-diagram-3", "plan", ""),
    (r"snapshot|status conclu|verifica[cç][aã]o de status|pontua[cç][aã]o", "bi-clipboard-data", "status", "game/buildings/townhall.png"),
    (r"constru|\bobras?\b|edif[ií]cio|demoli|\bn[ií]ve(?:l|is)\b", "bi-building", "construction", "game/buildings/architectsoffice.png"),
    (r"pesquisa|ensaio|cientista|academia", "bi-lightbulb", "research", "game/buildings/academy.png"),
    (r"pirat|captura|saque|roub", "bi-flag", "piracy", "game/buildings/piratefortress.png"),
    (r"mercado|oferta|compra|venda|pre[cç]o", "bi-shop", "market", "game/buildings/tradingpost.png"),
    (r"ataque|hostis|advisor militar|tropa|frota|espi", "bi-shield-exclamation", "military", "game/buildings/barracks.png"),
    (r"mensagem|inbox|diplomacia|tratado", "bi-envelope", "diplomacy", "game/buildings/embassy.png"),
    (r"santu[aá]rio|milagre|\bfavor\b|templo|\bdeus", "bi-stars", "temple", "game/gods/favor.png"),
    (r"\bvinho\b|taberna|taverna", "bi-cup-straw", "wine", "game/buildings/tavern.png"),
    (r"\bstatus\b|cidade|servidor|\bconta\b", "bi-clipboard-data", "status", ""),
    (r"\bplano\b", "bi-diagram-3", "plan", ""),
    (r"conclu[ií]d|sucesso|\bok\b|enviado|confirmad", "bi-check-circle-fill", "done", ""),
)
_FAMILIES = tuple((re.compile(pattern, re.IGNORECASE), icon, family, image) for pattern, icon, family, image in FAMILIES)
_TITLE_RESOURCE = re.compile(r"\b(madeira|vinho|m[aá]rmore|cristal|enxofre)\b", re.IGNORECASE)


def human_duration(seconds) -> str:
    """87286 -> "1d", 3912 -> "1h 5min", 746 -> "12min 26s", 32 -> "32s"."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return str(seconds)
    if total < 0:
        total = 0
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}min {secs}s" if secs else f"{minutes}min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def _dots(number: str) -> str:
    return f"{int(number):,}".replace(",", ".")


def _thousands(text: str) -> str:
    """Numeros gravados a inglesa (9,264) no formato brasileiro (9.264)."""
    return _ENGLISH_NUMBER.sub(lambda match: match.group(1).replace(",", "."), text)


def _recase(word: str, fixed: str) -> str:
    if word.isupper() and len(word) > 1:
        return fixed.upper()
    return fixed[0].upper() + fixed[1:] if word[0].isupper() else fixed


def _accent(text: str) -> str:
    text = _ACCENT_RE.sub(lambda match: _recase(match.group(1), ACCENTS[match.group(1).lower()]), text)

    def by_suffix(match):
        word = match.group(0)
        if word.lower() in _SUFFIX_SKIP or not match.group(2).islower():
            return word
        return match.group(1) + _SUFFIXES[match.group(2)]
    return _SUFFIX_RE.sub(by_suffix, text)


def fix_accents(text: str) -> str:
    """Nomes do catalogo gravados sem acento ("Doacao em Loop") do jeito que se escreve."""
    return _accent(str(text or ""))


def _clean_text(text: str) -> str:
    text = _UUID_RE.sub(lambda match: match.group(0)[:8], str(text or ""))
    text = _thousands(text).replace(" -> ", " → ").replace("->", "→")
    text = _INLINE_SECONDS.sub(lambda match: human_duration(match.group(1)), text)
    text = _CODE.sub(lambda match: VALUE_LABELS.get(match.group(0), match.group(0)), text)
    text = _VERB_ESTA.sub(lambda match: ("Está" if match.group(1)[0] == "E" else "está") + match.group(2), _accent(text))
    return text.strip()


def _translate(message: str) -> str:
    for pattern, replacement in PHRASES:
        message = pattern.sub(replacement, message)
    return message


class LogContext:
    """O que e preciso saber para trocar codigos por nomes: cidades da conta do job."""

    def __init__(self, city_names: dict | None = None):
        self.city_names = {str(key): str(value) for key, value in (city_names or {}).items() if value}

    @classmethod
    def for_game_account(cls, game_account) -> "LogContext":
        return cls() if game_account is None else cls.for_game_accounts([game_account.pk])

    @classmethod
    def for_game_accounts(cls, game_account_ids) -> "LogContext":
        """Cidades de varias contas de uma vez (um workflow pode ter jobs de mais de uma)."""
        ids = [pk for pk in set(game_account_ids) if pk]
        if not ids:
            return cls()
        from apps.game.models import AccountSnapshot

        names = {}
        for cities in AccountSnapshot.objects.filter(game_account_id__in=ids).values_list("cities", flat=True):
            for city in cities if isinstance(cities, list) else []:
                if isinstance(city, dict) and city.get("id") is not None:
                    names[str(city.get("id"))] = city.get("name") or ""
        return cls(names)

    def city(self, value) -> str:
        return self.city_names.get(str(value).strip(), "")


def _cities_in_text(text: str, context: LogContext) -> str:
    """Troca codigos de cidade pelo nome onde a cidade e conhecida; o que nao e conhecido fica como esta."""
    text = re.sub(r"\((?:id|city_id)=(\d+)\)", lambda m: f"({context.city(m.group(1))})" if context.city(m.group(1)) else m.group(0), text)
    text = re.sub(r"\b(\d{3,8}) \(\1\)", lambda m: context.city(m.group(1)) or m.group(1), text)
    source = text

    def named(match):
        name = context.city(match.group(1))
        if not name:
            return match.group(0)
        return "" if source[:match.start()].rstrip().endswith(name) else f" ({name})"
    text = re.sub(r" \((\d{4,8})\)", named, source)          # "Hell (39272)" -> "Hell"
    text = re.sub(r"\b[Cc]idade (\d{4,8})\b", lambda m: context.city(m.group(1)) or m.group(0), text)
    return re.sub(r"(?<![\w.,/])(\d{4,8})(?![\w.,/])", lambda m: context.city(m.group(1)) or m.group(1), text)


def _field(label: str, value: str, *, kind: str = "text", icon_url: str = "", href: str = "", title: str = "") -> dict:
    return {"label": label, "value": value, "kind": kind, "icon_url": icon_url, "href": href, "title": title}


def _label(key: str) -> str:
    known = KEY_LABELS.get(key.strip().lower())
    if known:
        return known
    words = re.sub(
        r"\b(wood|wine|marble|crystal|glass|sulfur|gold)\b",
        lambda m: RESOURCES[m.group(1).lower()][0].lower(), key.replace("_", " ").strip(), flags=re.IGNORECASE,
    )
    return _accent(words[:1].upper() + words[1:])


def _format_value(key: str, value: str, context: LogContext, moment=None) -> dict:
    key_norm = (key or "").strip().lower()
    label = _label(key or "")
    raw = str(value or "").strip()

    seconds = _SECONDS.match(raw)
    if seconds:
        amount = float(seconds.group(1))
        title = raw
        if moment is not None and (key_norm in ("proximo_em", "check_em", "eta", "retry_em") or amount >= 3600):
            when = timezone.localtime(moment + timedelta(seconds=amount))
            title = f"{raw} — por volta de {when.strftime('%d/%m %H:%M')}"
        return _field(label, human_duration(amount), kind="duration", title=title)
    minutes = _MINUTES.match(raw)
    if minutes:
        return _field(label, human_duration(int(minutes.group(1)) * 60), kind="duration", title=raw)

    if key_norm in CITY_KEYS and raw.isdigit():
        name = context.city(raw)
        if name:
            return _field(label, name, kind="city", title=f"cidade {raw}")
    if re.fullmatch(_UUID, raw):
        if key_norm in JOB_KEYS:
            return _field(label, raw[:8], kind="link", href=reverse("jobs:job-detail", args=[raw]), title="abrir o job")
        if key_norm in ORDER_KEYS:
            return _field(label, raw[:8], kind="link", href=reverse("market:order-detail", args=[raw]), title="abrir a ordem")
        return _field(label, raw[:8], title=raw)

    resource = RESOURCES.get(raw.lower())
    if resource:
        return _field(label, resource[0], kind="resource", icon_url=static(resource[1]))
    nested = _NESTED_KV.match(raw)
    text = f"{nested.group(1)} {nested.group(2)}" if nested else raw    # "estimado=madeira=47.643" -> "madeira 47.643"
    if not (key_norm in ID_KEYS or key_norm.endswith("_id")):
        text = _dots(text) if re.fullmatch(r"\d{4,}", text) else _BARE_NUMBER.sub(lambda match: _dots(match.group(1)), text)
    if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", text):
        text = VALUE_LABELS.get(text, text.replace("_", " "))    # codigo interno: pelo menos sem os tracos
    text = _clean_text(text)
    key_resource = RESOURCES.get(key_norm)
    if key_resource:
        return _field(key_resource[0], text, kind="resource", icon_url=static(key_resource[1]))
    return _field(label, text, kind="number" if re.fullmatch(r"[\d.,/ ]+", text) else "text")


def _loose_value(value: str, context: LogContext) -> dict:
    """Um trecho sem nome ("Enxofre x10400", "9 cidades")."""
    resource = RESOURCES.get(value.lower())
    if resource:
        return _field("", resource[0], kind="resource", icon_url=static(resource[1]))
    amount = _RESOURCE_AMOUNT.match(value)
    if amount:
        name, icon = RESOURCES[amount.group(1).lower()]
        digits = amount.group(2).replace(",", "").replace(".", "")
        return _field(name, _dots(digits) if digits.isdigit() else amount.group(2), kind="resource", icon_url=static(icon))
    return _field("", _clean_text(_cities_in_text(value, context)))


def _system_line(message: str, moment) -> tuple[str, list[dict]] | None:
    """Linhas do proprio executor, que chegam em ingles."""
    started = re.match(r"^Job started on (.+)$", message)
    if started:
        return "Execução iniciada", [_field("Agente", started.group(1).strip())]
    rescheduled = re.match(r"^Rescheduled in (\d+)s$", message)
    if rescheduled:
        seconds = int(rescheduled.group(1))
        fields = [_field("Daqui a", human_duration(seconds), kind="duration", title=f"{seconds}s")]
        if moment is not None:
            when = timezone.localtime(moment + timedelta(seconds=seconds))
            fields.append(_field("Quando", when.strftime("%d/%m %H:%M"), kind="text"))
        return "Próxima execução agendada", fields
    login = re.match(r"^Login: (\S+) \| (\S+) \| proxy (\S+)$", message)
    if login:
        return "Login no jogo", [_field("Servidor", login.group(1)), _field("Conta", login.group(2)), _field("Proxy", login.group(3))]
    if message.strip() == "Login OK":
        return "Login concluído", []
    cached = re.match(r"^Reutilizando sessao cached via proxy (\S+)$", message)
    if cached:
        return "Sessão reaproveitada, sem novo login", [_field("Proxy", cached.group(1))]
    if re.match(r"^Collecting daily login bonus for account ", message):
        return "Coletando o bônus diário de login", []
    return None


def _top_level_split(text: str) -> list[str]:
    """Separa em ", " sem entrar nos parenteses: "A (1, x=2), B (3)" -> ["A (1, x=2)", "B (3)"]."""
    pieces, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0 and text[index + 1:index + 2] == " ":
            pieces.append(text[start:index].strip())
            start = index + 1
    pieces.append(text[start:].strip())
    return [piece for piece in pieces if piece]


def _kv_list(text: str) -> list[tuple] | None:
    """"a=1, b=2, resto" -> [("a", "1"), ("b", "2, resto")]; None quando nao e uma lista de campos."""
    pieces = [piece.strip() for piece in text.split(", ")]
    if not _KV.match(pieces[0]):
        return None
    pairs: list[list] = []
    for piece in pieces:
        pair = _KV.match(piece)
        if pair:
            pairs.append([pair.group(1).strip(), pair.group(2).strip()])
        else:
            pairs[-1][1] += ", " + piece
    return [(key, value, False) for key, value in pairs]


def _split(message: str) -> tuple[str, list[tuple], list[str]]:
    """(titulo, [(chave ou None, valor, rotulo literal?)], [etiquetas entre colchetes])."""
    text = message.strip()
    tags = []
    while True:
        tag = _TAG.match(text)
        if not tag:
            break
        tags.append(tag.group(1).strip())
        text = text[tag.end():]
    parts = [part.strip() for part in text.split(" | ")]
    head, rest = parts[0], [part for part in parts[1:] if part]
    pairs: list[tuple] = []
    new_job = _JOB_AT_END.match(head)
    if new_job:
        # "novo job imediato criado: <uuid>" -> o job vira um campo com link
        head = new_job.group(1).strip()
        pairs.append(("job", new_job.group(2), False))

    with_kv = _HEAD_WITH_KV.match(head)
    with_parens = _HEAD_WITH_PARENS.match(head)
    if with_kv:
        head = with_kv.group("title").strip()
        listed = _kv_list(with_kv.group("kv").strip())
        if listed and len(listed) > 1:
            pairs.extend(listed)
        else:
            rest.insert(0, with_kv.group("kv").strip())
    elif _KV.match(head):
        head, rest = "", [head, *rest]
    elif with_parens and _kv_list(with_parens.group("kv")):
        # "Mercado indisponivel (cidade=Polis, falta=3039, motivo=...)"
        head = with_parens.group("title").strip()
        pairs.extend(_kv_list(with_parens.group("kv")))
    elif ": " in head:
        before, after = head.split(": ", 1)
        items = _top_level_split(after)
        named = [_NAMED_ITEM.match(item) for item in items]
        if items and all(named) and (len(items) > 1 or "=" in named[0].group(2)):
            # "Cidades em risco: TheTower (1366.9h, felicidade=23), Reflexo (...)"
            head = before
            pairs.extend((match.group(1).strip(), re.sub(r"(\w)=(\S)", r"\1 \2", match.group(2)), True) for match in named)
        elif len(items) > 1 and any(_KV.match(item) for item in items) and all(len(item) <= 48 for item in items):
            # "Status concluido: 9 cidades, ouro=63,497,766"
            head = before
            for item in items:
                pair = _KV.match(item)
                pairs.append((pair.group(1).strip(), pair.group(2).strip(), False) if pair else (None, item, False))

    for segment in rest:
        # "origem=A -> destino=B" sao dois campos
        pieces = [piece.strip() for piece in segment.split(" -> ")]
        if len(pieces) > 1 and all(_KV.match(piece) for piece in pieces):
            pairs.extend((match.group(1).strip(), match.group(2).strip(), False) for match in map(_KV.match, pieces))
            continue
        pair = _KV.match(segment)
        pairs.append((pair.group(1).strip(), pair.group(2).strip(), False) if pair else (None, segment, False))
    return head, pairs, tags


def _title(head: str, context: LogContext) -> str:
    text = head.strip().lstrip("↳•–> ").rstrip(":").strip()
    text = _cities_in_text(text, context)
    text = re.sub(
        r"\b(wood|wine|marble|crystal|sulfur)\b",
        lambda m: RESOURCES[m.group(1).lower()][0], text, flags=re.IGNORECASE,
    )
    text = _clean_text(text)
    for cryptic, clear in TITLE_REWRITES:
        if text.lower().startswith(cryptic):
            text = clear + text[len(cryptic):]
            break
    return text


def _without_repeats(fields: list[dict]) -> list[dict]:
    """"cidade=Hell | city_id=39272" diz a mesma coisa duas vezes: fica a primeira."""
    kept: list[dict] = []
    named_building = any(field["label"] in ("Prédio", "Edifício") for field in fields)
    for field in fields:
        if named_building and field["label"] == "Edifício (id)":
            continue        # o codigo interno do predio cujo nome ja esta ali
        same_label = [other for other in kept if other["label"] == field["label"] and field["label"]]
        if any(other["value"] == field["value"] for other in same_label):
            continue
        if same_label and field["label"] == "Cidade" and field["value"].isdigit():
            continue        # o codigo da cidade cujo nome ja esta ali
        kept.append(field)
    return kept


def _tag(text: str) -> dict:
    """Etiqueta entre colchetes: "Order <uuid>" -> "Ordem 0b54e6c2", com o link da ordem."""
    href = ""
    order = re.fullmatch(rf"Order ({_UUID})", text.strip())
    if order:
        href = reverse("market:order-detail", args=[order.group(1)])
    return {"text": _clean_text(re.sub(r"^Order\b", "Ordem", text)), "href": href}


def format_log(level: str, message: str, *, context: LogContext | None = None, moment=None) -> dict:
    """Uma linha de log pronta para a tela.

    {"level", "level_label", "color", "icon", "image_url", "family", "title", "fields", "tags", "raw"}
    """
    context = context or LogContext()
    level = {"warning": "warn", "err": "error"}.get(str(level or "info").lower(), str(level or "info").lower())
    if level not in LEVELS:
        level = "info"
    raw = str(message or "")

    system = _system_line(raw, moment)
    if system:
        title, fields = system
        tags: list[str] = []
    else:
        head, pairs, tags = _split(_translate(raw))
        title = _title(head, context)
        if tags and title[:1].islower():
            title = title[:1].upper() + title[1:]      # a frase comecava depois da etiqueta
        fields = []
        group = None    # "estimado=madeira=47.643 | mármore=42.948": os recursos seguintes pertencem ao mesmo campo
        for key, value, literal in pairs:
            if group is not None and key is not None and not literal and key.strip().lower() in RESOURCES:
                group["value"] += f" · {RESOURCES[key.strip().lower()][0].lower()} {_clean_text(value)}"
                continue
            group = None
            if key is None:
                fields.append(_loose_value(value, context))
            elif literal:
                fields.append(_field(key, _clean_text(value)))
            else:
                fields.append(_format_value(key, value, context, moment))
                nested = _NESTED_KV.match(value.strip())
                if nested and nested.group(1).lower() in RESOURCES and key.strip().lower() not in RESOURCES:
                    group = fields[-1]
        fields = _without_repeats(fields)
        if not title and fields:
            first = fields.pop(0)
            title = f"{first['label']}: {first['value']}" if first["label"] else first["value"]

    family, icon, image = "other", LEVELS[level]["icon"], ""
    probe = f"{raw} {title}"
    for pattern, family_icon, family_name, family_image in _FAMILIES:
        if pattern.search(probe):
            family, icon, image = family_name, family_icon, family_image
            break
    # a linha fala de um recurso so: o icone dele diz mais que o do assunto
    mentioned = {RESOURCES[name.lower().replace("á", "a")][1] for name in _TITLE_RESOURCE.findall(title or "")}
    if len(mentioned) == 1:
        image = mentioned.pop()
    if level in ("error", "warn"):
        icon, image = LEVELS[level]["icon"], ""   # um problema e um problema, qualquer que seja o assunto
    return {
        "level": level,
        "level_label": LEVELS[level]["label"],
        "color": LEVELS[level]["color"],
        "icon": icon,
        "image_url": static(image) if image else "",
        "family": family,
        "title": title or raw,
        "fields": fields,
        "tags": [_tag(tag) for tag in tags],
        "raw": raw,
    }


GAP_SECONDS = 120


def build_entries(logs, *, context: LogContext | None = None) -> list[dict]:
    """As linhas de uma execucao, na ordem em que aconteceram.

    Cada item: {"log", "fmt", "delta" (tempo desde a linha anterior), "gap" (pausa longa
    antes dela, para a tela marcar), "restart" (o job foi executado de novo a partir daqui)}.
    """
    entries = []
    previous = None
    for index, log in enumerate(logs):
        try:
            fmt = format_log(log.level, log.message, context=context, moment=log.created_at)
        except Exception:
            # uma linha estranha nunca derruba a pagina: aparece como foi gravada
            fmt = {**format_log(log.level, ""), "title": str(log.message or ""), "raw": str(log.message or "")}
        seconds = max(0.0, (log.created_at - previous).total_seconds()) if previous is not None else 0.0
        entries.append({
            "log": log,
            "fmt": fmt,
            "delta": human_duration(seconds) if seconds >= 1 else "",
            "gap": human_duration(seconds) if seconds >= GAP_SECONDS else "",
            "restart": index > 0 and fmt["family"] == "start",
        })
        previous = log.created_at
    return entries


def level_counts(entries) -> list[dict]:
    """[{"level", "label", "color", "icon", "count"}] na ordem erro, aviso, info, debug (so os que existem)."""
    totals = dict.fromkeys(LEVEL_ORDER, 0)
    for entry in entries:
        totals[entry["fmt"]["level"]] += 1
    return [
        {"level": level, "label": LEVELS[level]["label"], "color": LEVELS[level]["color"], "icon": LEVELS[level]["icon"], "count": totals[level]}
        for level in LEVEL_ORDER if totals[level]
    ]
