"""
Models: TelegramConfig, MessageAudit — notification management.
"""

import secrets

from django.db import models

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
            ("unlinked", "Nao vinculado"),
            ("pending", "Aguardando confirmacao"),
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
            ("unlinked", "Nao vinculado"),
            ("pending", "Aguardando confirmacao"),
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

    DEFAULTS = {
        "attack_alert": {
            "icon": "⚠️",
            "title_template": "Alerta de Ataque",
            "body_template": "{ga_name} esta sob ataque!",
        },
        "job_failed": {
            "icon": "❌",
            "title_template": "{action_name} falhou",
            "body_template": "Conta: {ga_name}\nExit code: {exit_code}",
        },
        "job_done": {
            "icon": "✅",
            "title_template": "{action_name} concluido",
            "body_template": "Conta: {ga_name}",
        },
        "build_complete": {
            "icon": "🏗️",
            "title_template": "Construcao concluida",
            "body_template": "{ga_name} — {server_id}",
        },
        "research_complete": {
            "icon": "💡",
            "title_template": "Pesquisa concluida",
            "body_template": "{ga_name} — {server_id}",
        },
        "low_wine": {
            "icon": "🍷",
            "title_template": "Vinho baixo",
            "body_template": "{ga_name} esta com estoque baixo de vinho.",
        },
        "daily_summary": {
            "icon": "📊",
            "title_template": "Resumo diario",
            "body_template": "{account_name}",
        },
    }

    # All available variables with descriptions (for UI reference)
    AVAILABLE_VARS = [
        ("{action_name}", "Nome da acao (ex: Check Status)"),
        ("{ga_name}", "Nome da subconta"),
        ("{server_id}", "ID do servidor (ex: s61-br)"),
        ("{account_name}", "Nome da conta lobby"),
        ("{node_name}", "Nome do node"),
        ("{agent_name}", "Nome do agent"),
        ("{exit_code}", "Codigo de saida (jobs)"),
        ("{job_id}", "ID do job"),
        ("{status}", "Status (finished, error)"),
        ("{error}", "Mensagem de erro"),
    ]

    event_key = models.CharField(max_length=32, unique=True)
    icon = models.CharField(max_length=8, default="🔔")
    title_template = models.CharField(
        max_length=255,
        default="{action_name}",
        help_text="Titulo da mensagem. Aceita variaveis como {action_name}, {ga_name}, etc.",
    )
    body_template = models.TextField(
        blank=True,
        default="",
        help_text="Corpo da mensagem. Aceita variaveis e quebras de linha.",
    )

    class Meta:
        ordering = ["event_key"]
        verbose_name = "Template de Notificacao"
        verbose_name_plural = "Templates de Notificacao"

    def __str__(self):
        return f"{self.icon} {self.event_key}"

    def render(self, **kwargs):
        """
        Render this template with the given context.

        Returns the full HTML message ready for Telegram.
        """
        ctx = _SafeDict({
            "action_name": kwargs.get("action_name", ""),
            "ga_name": kwargs.get("ga_name", ""),
            "server_id": kwargs.get("server_id", ""),
            "account_name": kwargs.get("account_name", ""),
            "node_name": kwargs.get("node_name", ""),
            "agent_name": kwargs.get("agent_name", ""),
            "title": kwargs.get("title", ""),
            "body": kwargs.get("body", ""),
            "exit_code": kwargs.get("exit_code", ""),
            "job_id": kwargs.get("job_id", ""),
            "status": kwargs.get("status", ""),
            "error": kwargs.get("error", ""),
        })
        for key, value in kwargs.items():
            if key in ctx:
                continue
            if value is None:
                ctx[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                ctx[key] = value
            else:
                ctx[key] = str(value)

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
                event_key=key,
                defaults={
                    "icon": defaults["icon"],
                    "title_template": defaults["title_template"],
                    "body_template": defaults["body_template"],
                },
            )


class _SafeDict(dict):
    """Dict that returns empty string for missing keys in str.format_map()."""

    def __missing__(self, key):
        return ""


class MessageAudit(models.Model):
    """
    Audit trail for sent notifications.
    """

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.provider}] {self.title[:50]} ({self.status})"
