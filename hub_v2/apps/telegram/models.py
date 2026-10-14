"""
Models: TelegramConfig, MessageAudit — notification management.
"""

import secrets

from django.db import models

from apps.orgs.scope import OrgScopedManager

from core.mixins.models import TimestampModel


class TelegramBotConfig(TimestampModel):
    """
    Global Telegram bot configuration + default chat.

    Only one instance should exist (singleton pattern via get_or_create).
    The global chat_id is used for all notifications unless a
    TelegramAccountConfig overrides it with its own chat_id.
    """

    bot_token = models.CharField(max_length=255, blank=True, default="")
    enabled = models.BooleanField(default=False)
    webhook_secret = models.CharField(max_length=64, blank=True, default="")

    # Global chat — default destination for all notifications
    chat_id = models.CharField(
        max_length=64, blank=True, default="",
        verbose_name="Chat ID global",
    )
    telegram_username = models.CharField(max_length=64, blank=True, default="")
    link_code = models.CharField(max_length=6, blank=True, default="")
    link_code_created_at = models.DateTimeField(null=True, blank=True)
    link_status = models.CharField(
        max_length=16,
        default="unlinked",
        choices=[
            ("unlinked", "Não vinculado"),
            ("pending", "Aguardando confirmação"),
            ("linked", "Vinculado"),
        ],
    )

    # Global notification defaults
    notify_attack_alert = models.BooleanField(default=True)
    notify_job_failed = models.BooleanField(default=True)
    notify_job_done = models.BooleanField(default=False)
    notify_build_complete = models.BooleanField(default=False)
    notify_research_complete = models.BooleanField(default=False)
    notify_low_wine = models.BooleanField(default=True)
    notify_daily_summary = models.BooleanField(default=False)
    notify_diplomacy_message = models.BooleanField(default=True)
    notify_island_monitor = models.BooleanField(default=True)
    notify_raid_alert = models.BooleanField(default=True)
    notify_cinema_available = models.BooleanField(default=False)
    notify_combat_report = models.BooleanField(default=False)
    notify_proxy_change = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Configuração do Bot Telegram"
        verbose_name_plural = "Configuração do Bot Telegram"

    def __str__(self):
        return f"Telegram Bot ({'ativo' if self.enabled else 'inativo'})"

    @property
    def is_linked(self):
        return bool(self.chat_id)

    @property
    def is_active(self):
        return self.enabled and bool(self.bot_token)

    def save(self, *args, **kwargs):
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_hex(16)
        super().save(*args, **kwargs)


class TelegramAccountConfig(TimestampModel):
    """
    Per-GameAccount (subconta) Telegram notification preferences.

    Cada subconta (servidor) pode ter configuracoes independentes:
    chat_id diferente, toggles diferentes, etc.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    game_account = models.OneToOneField(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="telegram_config",
    )
    chat_id = models.CharField(max_length=64, blank=True, default="")
    enabled = models.BooleanField(default=False)

    # Linking fields
    link_code = models.CharField(max_length=6, blank=True, default="")
    link_code_created_at = models.DateTimeField(null=True, blank=True)
    link_status = models.CharField(
        max_length=16,
        default="unlinked",
        choices=[
            ("unlinked", "Não vinculado"),
            ("pending", "Aguardando confirmação"),
            ("linked", "Vinculado"),
        ],
    )
    telegram_username = models.CharField(max_length=64, blank=True, default="")

    # Event toggles
    notify_attack_alert = models.BooleanField(default=True)
    notify_job_failed = models.BooleanField(default=True)
    notify_job_done = models.BooleanField(default=False)
    notify_build_complete = models.BooleanField(default=False)
    notify_research_complete = models.BooleanField(default=False)
    notify_low_wine = models.BooleanField(default=True)
    notify_daily_summary = models.BooleanField(default=False)
    notify_diplomacy_message = models.BooleanField(default=True)
    notify_island_monitor = models.BooleanField(default=True)
    notify_raid_alert = models.BooleanField(default=True)
    notify_cinema_available = models.BooleanField(default=False)
    notify_combat_report = models.BooleanField(default=False)
    notify_proxy_change = models.BooleanField(default=False)

    @property
    def has_custom_chat(self):
        """True if this subconta overrides the global chat_id."""
        return bool(self.chat_id)

    def get_effective_chat_id(self, bot_config=None):
        """Return this subconta's chat_id, or fall back to the global one."""
        if self.chat_id:
            return self.chat_id
        if bot_config is None:
            bot_config, _ = TelegramBotConfig.objects.get_or_create(pk=1)
        return bot_config.chat_id

    def __str__(self):
        return f"Telegram config for GA {self.game_account_id}"


class TelegramIncomingCommand(TimestampModel):
    """
    Configurable Telegram inbound commands.

    The webhook still owns the parsing logic for each command type, but the
    command text can be changed from the Telegram configuration screen.
    """

    COMMAND_LINK = "link"
    COMMAND_DIPLOMACY_REPLY = "diplomacy_reply"

    COMMAND_TYPE_CHOICES = [
        (COMMAND_LINK, "Vincular chat"),
        (COMMAND_DIPLOMACY_REPLY, "Responder diplomacia"),
    ]

    DEFAULTS = {
        COMMAND_LINK: {
            "command": "/start",
            "description": "Vincula um chat usando o código gerado no painel.",
        },
        COMMAND_DIPLOMACY_REPLY: {
            "command": "/replyto",
            "description": "Cria job de resposta ou ação para mensagem de diplomacia.",
        },
    }

    key = models.CharField(max_length=32, unique=True, choices=COMMAND_TYPE_CHOICES)
    command = models.CharField(max_length=32, default="")
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["key"]
        verbose_name = "Comando de Entrada Telegram"
        verbose_name_plural = "Comandos de Entrada Telegram"

    def __str__(self):
        return f"{self.command} ({self.key})"

    @classmethod
    def seed_defaults(cls):
        for key, defaults in cls.DEFAULTS.items():
            cls.objects.get_or_create(
                key=key,
                defaults={
                    "command": defaults["command"],
                    "description": defaults["description"],
                },
            )

    @classmethod
    def command_for(cls, key: str) -> str:
        defaults = cls.DEFAULTS.get(key, {})
        default_command = defaults.get("command", "")
        try:
            item = cls.objects.filter(key=key).first()
        except Exception:
            return default_command
        if item and not item.enabled:
            return ""
        return (item.command if item else default_command).strip() or default_command


class NotificationTemplate(TimestampModel):
    """
    Customizable message templates per event type.

    Separated into icon, title_template and body_template for easy UI editing.
    Templates use Python str.format_map() with safe fallback for missing keys.

    Available variables:
      {action_name}  — Nome da acao (ex: Check Status, Doacao)
      {ga_name}      — Nome da subconta (ex: Cidade01)
      {server_id}    — ID do servidor (ex: s61-br)
      {account_name} — Nome da conta lobby
      {node_name}    — Nome do node/agente
      {agent_name}   — Nome do processo agent
      {exit_code}    — Codigo de saida do job
      {job_id}       — UUID do job
      {status}       — Status do job (finished, error)
      {error}        — Mensagem de erro (se houver)
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    DEFAULTS = {
        "diplomacy_message": {
            "icon": "\U0001f4e8",
            "title_template": "Diplomacia - {ga_name}",
            "body_template": (
                "De: {sender}\n"
                "Assunto: {subject}\n"
                "Data: {game_date}\n"
                "{message_body}\n"
                "Responder: <code>{reply_command}</code>\n"
                "Aceitar: <code>{accept_command}</code>\n"
                "Recusar: <code>{decline_command}</code>"
            ),
        },
        # N-109: written from what the alert carries, not from a ready-made line
        "attack_alert": {
            "icon": "🆘",
            "title_template": "{mission} em {target_city}",
            "body_template": "{ga_name} · {server_id}\n{origin_player} ({origin_city}) → {target_city}\nTropas: {troops} · Frotas: {fleet}\nChegada em {time_left_human}",
        },
        "job_failed": {
            "icon": "❌",
            "title_template": "{action_name} falhou",
            "body_template": "{ga_name} · {server_id}\n{city_name}\nNó: {node_name} · saída {exit_code}\n{body}",
        },
        "job_done": {
            "icon": "✅",
            "title_template": "{action_name} concluído",
            "body_template": "{ga_name} · {server_id}\n{city_name}",
        },
        # N-109: a round or the end of a combat; the picture of the battlefield goes with it
        "combat_report": {
            "icon": "⚔️",
            "title_template": "{headline} · {city}",
            "body_template": "{versus}\n{losses}\n{loot_line}",
        },
        "proxy_change": {
            "icon": "🔁",
            "title_template": "Proxies: verificação automática",
            "body_template": "{changes}\n{provider}\n{tests}\n{reason_line}",
        },
        "build_complete": {
            "icon": "🏗️",
            "title_template": "Construção concluída",
            "body_template": "{ga_name} — {server_id}",
        },
        "research_complete": {
            "icon": "💡",
            "title_template": "Pesquisa concluída",
            "body_template": "{ga_name} — {server_id}",
        },
        "low_wine": {
            "icon": "🍷",
            "title_template": "Vinho baixo",
            "body_template": "{ga_name} esta com estoque baixo de vinho.",
        },
        "daily_summary": {
            "icon": "📊",
            "title_template": "Resumo diário",
            "body_template": "{account_name}",
        },
        # N-104: the island notice is built from what changed, not from a ready-made text
        "island_monitor": {
            "icon": "🏝️",
            "title_template": "Mudança na ilha {coords}",
            "body_template": "{island_line}\nVisto por {ga_name}\n{changes}",
        },
        # N-104: one message a day for all the accounts, an hour before the game day turns
        "cinema_available": {
            "icon": "🎬",
            "title_template": "Cineteatro disponível em {count} conta(s)",
            "body_template": "{accounts}\nO dia do jogo vira às {reset_time}. A sessão é manual no jogo.",
        },
    }
    # what the island template was before N-104, to upgrade rows nobody edited
    OLD_ISLAND_DEFAULT = ("{title}", "{ga_name} | {server_id}\n{body}")

    # N-104: the variables each notice fills, shown next to its editor
    COMMON_VARS = [
        ("{ga_name}", "Nome da conta do jogo"),
        ("{server_id}", "Servidor (ex.: s78-br)"),
        ("{account_name}", "Conta do lobby"),
    ]
    # notices about the system, not about one game account: the common variables stay out
    SYSTEM_EVENTS = {"proxy_change"}
    EVENT_VARS = {
        "attack_alert": [
            ("{mission}", "Missão do inimigo (ex.: Roubar)"),
            ("{target_city}", "Cidade atacada"),
            ("{origin_player}", "Quem ataca"),
            ("{origin_city}", "Cidade de onde vem"),
            ("{troops}", "Quantas tropas"),
            ("{fleet}", "Quantos navios"),
            ("{time_left_human}", "Quanto falta para chegar"),
        ],
        "job_failed": [
            ("{action_name}", "Ação do job"),
            ("{city_name}", "Cidade do job, quando ele tem uma"),
            ("{node_name}", "Nó onde rodou"),
            ("{exit_code}", "Código de saída"),
            ("{body}", "Última linha de erro do job"),
            ("{job_id}", "Identificador do job"),
        ],
        "job_done": [
            ("{action_name}", "Ação do job"),
            ("{city_name}", "Cidade do job, quando ele tem uma"),
            ("{node_name}", "Nó onde rodou"),
            ("{job_id}", "Identificador do job"),
        ],
        "combat_report": [
            ("{headline}", "\"Round 2 de 5\" durante o combate; \"Vitória em 5 rounds\" no fim"),
            ("{city}", "Cidade do combate"),
            ("{versus}", "Atacante × defensor"),
            ("{attacker}", "Atacante"),
            ("{defender}", "Defensor"),
            ("{losses}", "Perdas dos dois lados, uma linha cada"),
            ("{attacker_losses}", "Perdas do atacante"),
            ("{defender_losses}", "Perdas do defensor"),
            ("{loot_line}", "\"Saque: ...\", só no fim e quando houve"),
            ("{result}", "Vitória ou Derrota, só no fim"),
            ("{round}", "Número do round"),
            ("{total_rounds}", "Total de rounds"),
            ("{date}", "Data do round no jogo"),
        ],
        "proxy_change": [
            ("{changes}", "O que mudou, uma linha por conta ou nó"),
            ("{provider}", "O que veio da lista do provedor"),
            ("{tests}", "Quantos proxies passaram no teste"),
            ("{reason_line}", "Por que a verificação rodou"),
        ],
        "cinema_available": [
            ("{count}", "Quantas contas têm sessão disponível"),
            ("{accounts}", "Uma linha por conta: nome e cidade"),
            ("{reset_time}", "Hora em que o dia do jogo vira"),
            ("{reset_in}", "Quanto falta para virar (ex.: 1h)"),
        ],
        "island_monitor": [
            ("{coords}", "Coordenadas da ilha"),
            ("{island_line}", "Ilha, recurso e maravilha numa linha"),
            ("{island}", "Nome e coordenadas da ilha"),
            ("{resource}", "Recurso da ilha"),
            ("{miracle}", "Maravilha da ilha"),
            ("{changes}", "Uma linha por mudança (cidade nova, sumiu, trocou de dono, batalha)"),
            ("{changes_count}", "Quantas mudanças"),
        ],
        "diplomacy_message": [
            ("{sender}", "Remetente"),
            ("{subject}", "Assunto"),
            ("{game_date}", "Data no jogo"),
            ("{message_body}", "Texto da mensagem"),
            ("{reply_command}", "Comando para responder"),
            ("{accept_command}", "Comando para aceitar"),
            ("{decline_command}", "Comando para recusar"),
        ],
    }

    # All available variables with descriptions (for UI reference)
    AVAILABLE_VARS = [
        ("{action_name}", "Nome da ação (ex: Check Status)"),
        ("{ga_name}", "Nome da subconta"),
        ("{server_id}", "ID do servidor (ex: s61-br)"),
        ("{account_name}", "Nome da conta lobby"),
        ("{node_name}", "Nome do node"),
        ("{agent_name}", "Nome do agent"),
        ("{exit_code}", "Código de saída (jobs)"),
        ("{job_id}", "ID do job"),
        ("{status}", "Status (finished, error)"),
        ("{error}", "Mensagem de erro"),
        ("{sender}", "Remetente da mensagem"),
        ("{subject}", "Assunto da mensagem"),
        ("{message_body}", "Corpo da mensagem de diplomacia"),
        ("{reply_command}", "Comando para responder"),
        ("{accept_command}", "Comando para aceitar"),
        ("{decline_command}", "Comando para recusar"),
    ]

    event_key = models.CharField(max_length=32, db_index=True)
    # N-104: empty = the global text; set = the text only this account uses instead of it
    game_account = models.ForeignKey(
        "accounts.GameAccount", on_delete=models.CASCADE, null=True, blank=True, related_name="telegram_templates",
    )
    # "<event>:global" or "<event>:<account id>": one row per event and scope (MariaDB has no
    # conditional unique constraint, and NULLs do not collide in a plain one)
    scope_key = models.CharField(max_length=80, unique=True, null=True, blank=True, editable=False)
    icon = models.CharField(max_length=8, default="🔔")
    title_template = models.CharField(
        max_length=255,
        default="{action_name}",
        help_text="Título da mensagem. Aceita variáveis como {action_name}, {ga_name}, etc.",
    )
    body_template = models.TextField(
        blank=True,
        default="",
        help_text="Corpo da mensagem. Aceita variáveis e quebras de linha.",
    )

    class Meta:
        ordering = ["event_key"]
        verbose_name = "Template de Notificação"
        verbose_name_plural = "Templates de Notificação"

    def __str__(self):
        return f"{self.icon} {self.event_key}"

    @staticmethod
    def scope_for(event_key: str, game_account_id=None) -> str:
        return f"{event_key}:{game_account_id or 'global'}"

    def save(self, *args, **kwargs):
        self.scope_key = self.scope_for(self.event_key, self.game_account_id)
        super().save(*args, **kwargs)

    @classmethod
    def for_event(cls, event_key: str, game_account=None):
        """The template a notice uses: the account's own text when it has one, else the global."""
        if game_account is not None:
            own = cls.objects.filter(scope_key=cls.scope_for(event_key, game_account.pk)).first()
            if own:
                return own
        return cls.objects.filter(scope_key=cls.scope_for(event_key)).first()

    @classmethod
    def variables_for(cls, event_key: str) -> list[tuple[str, str]]:
        if event_key not in cls.EVENT_VARS:
            return cls.AVAILABLE_VARS
        return [*cls.EVENT_VARS[event_key], *([] if event_key in cls.SYSTEM_EVENTS else cls.COMMON_VARS)]

    def unknown_variables(self) -> list[str]:
        """Names between braces that this notice never fills (a typo shows as an empty space)."""
        import re

        known = {name.strip("{}") for name, _label in self.variables_for(self.event_key)}
        known |= {name.strip("{}") for name, _label in self.AVAILABLE_VARS} | {"title", "body"}
        used = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", f"{self.title_template}\n{self.body_template}"))
        return sorted(used - known)

    def reset_to_default(self) -> bool:
        defaults = self.DEFAULTS.get(self.event_key)
        if not defaults:
            return False
        self.icon, self.title_template, self.body_template = defaults["icon"], defaults["title_template"], defaults["body_template"]
        self.save()
        return True

    def render(self, **kwargs):
        """
        Render this template with the given context.

        Returns the full HTML message ready for Telegram.
        """
        import html as _html

        def _esc(v):
            return _html.escape(str(v)) if v is not None else ""

        ctx = _SafeDict({
            "action_name": _esc(kwargs.get("action_name", "")),
            "ga_name": _esc(kwargs.get("ga_name", "")),
            "server_id": _esc(kwargs.get("server_id", "")),
            "account_name": _esc(kwargs.get("account_name", "")),
            "node_name": _esc(kwargs.get("node_name", "")),
            "agent_name": _esc(kwargs.get("agent_name", "")),
            "title": _esc(kwargs.get("title", "")),
            "body": _esc(kwargs.get("body", "")),
            "exit_code": _esc(kwargs.get("exit_code", "")),
            "job_id": _esc(kwargs.get("job_id", "")),
            "status": _esc(kwargs.get("status", "")),
            "error": _esc(kwargs.get("error", "")),
        })
        for key, value in kwargs.items():
            if key in ctx:
                continue
            if value is None:
                ctx[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                ctx[key] = _esc(value)
            else:
                ctx[key] = _esc(str(value))

        try:
            title = self.title_template.format_map(ctx)
            body = self.body_template.format_map(ctx) if self.body_template else ""
        except (KeyError, ValueError):
            title = self.title_template
            body = self.body_template

        # Build message: icon + bold title + body
        lines = [f"{self.icon} <b>{title}</b>"]
        if body:
            # Remove lines that are entirely empty after variable substitution
            body_lines = [l for l in body.split("\n") if l.strip()]
            lines.extend(body_lines)
        return "\n".join(lines)

    @classmethod
    def seed_defaults(cls):
        """Create default templates for all event types (idempotent)."""
        for key, defaults in cls.DEFAULTS.items():
            cls.objects.get_or_create(
                scope_key=cls.scope_for(key),
                defaults={
                    "event_key": key,
                    "icon": defaults["icon"],
                    "title_template": defaults["title_template"],
                    "body_template": defaults["body_template"],
                },
            )


class _SafeDict(dict):
    """Dict that returns empty string for missing keys in str.format_map()."""

    def __missing__(self, key):
        return ""


class DigestEntry(TimestampModel):
    """What one account has to say in a notice that is sent once, for all accounts together (N-104).

    Cinema: every daily-login run of an account reports whether the cinema has a session; an
    hour before the game day turns the hub sends ONE message listing the accounts that still
    have it. A row per account and game day; `active` follows the latest report.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    event_key = models.CharField(max_length=32)
    game_account = models.ForeignKey("accounts.GameAccount", on_delete=models.CASCADE, related_name="telegram_digest_entries")
    day_key = models.CharField(max_length=16, help_text="Game day the entry belongs to (date of its reset, UTC)")
    active = models.BooleanField(default=True)
    due_at = models.DateTimeField(null=True, blank=True, help_text="When the digest should go out")
    reset_at = models.DateTimeField(null=True, blank=True)
    data_json = models.TextField(blank=True, default="{}")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("event_key", "game_account", "day_key")]
        indexes = [models.Index(fields=["event_key", "sent_at", "due_at"], name="tg_digest_due_idx")]

    def __str__(self):
        return f"{self.event_key} {self.game_account_id} {self.day_key}"


class MessageAudit(models.Model):
    """
    Audit trail for sent notifications.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    STATUS_CHOICES = [
        ("sent", "Enviado"),
        ("pending", "Pendente"),
        ("failed", "Falhou"),
        ("retried", "Reenviado"),
    ]

    node = models.ForeignKey(
        "accounts.Node", on_delete=models.SET_NULL, null=True, blank=True
    )
    account = models.ForeignKey(
        "accounts.Account", on_delete=models.SET_NULL, null=True, blank=True
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount", on_delete=models.SET_NULL, null=True, blank=True
    )
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True
    )
    provider = models.CharField(max_length=32, default="telegram")
    channel = models.CharField(max_length=32, default="notification")
    status = models.CharField(max_length=24, default="sent", choices=STATUS_CHOICES)
    agent_name = models.CharField(max_length=128, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    external_ref = models.CharField(max_length=128, blank=True, default="")
    context_json = models.TextField(blank=True, default="{}")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.provider}] {self.title[:50]} ({self.status})"
