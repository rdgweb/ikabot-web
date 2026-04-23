"""
Forms para o app Telegram.
"""

from django import forms

from .models import (
    TelegramBotConfig,
    TelegramAccountConfig,
    NotificationTemplate,
    TelegramIncomingCommand,
)
from .constants import EVENT_TYPES


class TelegramBotConfigForm(forms.ModelForm):
    """Form global — token do bot + habilitado."""

    class Meta:
        model = TelegramBotConfig
        fields = ["bot_token", "enabled"]
        widgets = {
            "bot_token": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            }),
        }
        labels = {
            "bot_token": "Token do Bot",
            "enabled": "Habilitado",
        }
        help_texts = {
            "bot_token": "Token fornecido pelo @BotFather no Telegram.",
        }


class TelegramGlobalNotificationForm(forms.ModelForm):
    """Form de toggles globais de notificacao (na config page)."""

    class Meta:
        model = TelegramBotConfig
        fields = [
            "notify_attack_alert",
            "notify_job_failed",
            "notify_job_done",
            "notify_build_complete",
            "notify_research_complete",
            "notify_low_wine",
            "notify_daily_summary",
            "notify_diplomacy_message",
        ]
        labels = {
            "notify_attack_alert": "Alerta de ataque",
            "notify_job_failed": "Job falhou",
            "notify_job_done": "Job concluido",
            "notify_build_complete": "Construcao concluida",
            "notify_research_complete": "Pesquisa concluida",
            "notify_low_wine": "Vinho baixo",
            "notify_daily_summary": "Resumo diario",
            "notify_diplomacy_message": "Mensagem de diplomacia",
        }


class TelegramAccountConfigForm(forms.ModelForm):
    """Form completo (admin) — inclui chat_id."""

    class Meta:
        model = TelegramAccountConfig
        fields = [
            "chat_id",
            "enabled",
            "notify_attack_alert",
            "notify_job_failed",
            "notify_job_done",
            "notify_build_complete",
            "notify_research_complete",
            "notify_low_wine",
            "notify_daily_summary",
            "notify_diplomacy_message",
        ]
        widgets = {
            "chat_id": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "ex: 123456789",
            }),
        }
        labels = {
            "chat_id": "Chat ID",
            "enabled": "Habilitado",
            "notify_attack_alert": "Alerta de ataque",
            "notify_job_failed": "Job falhou",
            "notify_job_done": "Job concluido",
            "notify_build_complete": "Construcao concluida",
            "notify_research_complete": "Pesquisa concluida",
            "notify_low_wine": "Vinho baixo",
            "notify_daily_summary": "Resumo diario",
            "notify_diplomacy_message": "Mensagem de diplomacia",
        }


class TelegramNotificationForm(forms.ModelForm):
    """
    Form inline (detalhe da conta) — apenas toggles de notificacao.

    chat_id e account nao aparecem aqui: chat_id e gerenciado pelo
    fluxo de vinculacao e account e injetado pela view.
    """

    class Meta:
        model = TelegramAccountConfig
        fields = [
            "enabled",
            "notify_attack_alert",
            "notify_job_failed",
            "notify_job_done",
            "notify_build_complete",
            "notify_research_complete",
            "notify_low_wine",
            "notify_daily_summary",
            "notify_diplomacy_message",
        ]
        labels = {
            "enabled": "Notificacoes ativadas",
            "notify_attack_alert": "Alerta de ataque",
            "notify_job_failed": "Job falhou",
            "notify_job_done": "Job concluido",
            "notify_build_complete": "Construcao concluida",
            "notify_research_complete": "Pesquisa concluida",
            "notify_low_wine": "Vinho baixo",
            "notify_daily_summary": "Resumo diario",
            "notify_diplomacy_message": "Mensagem de diplomacia",
        }


class NotificationTemplateForm(forms.ModelForm):
    """Form para editar template de notificacao individual."""

    class Meta:
        model = NotificationTemplate
        fields = ["icon", "title_template", "body_template"]
        widgets = {
            "icon": forms.TextInput(attrs={
                "class": "form-input w-16 text-center text-xl",
                "maxlength": "8",
            }),
            "title_template": forms.TextInput(attrs={
                "class": "form-input text-sm",
                "placeholder": "{action_name} concluido",
            }),
            "body_template": forms.Textarea(attrs={
                "class": "form-input font-mono text-sm",
                "rows": 2,
                "placeholder": "Conta: {ga_name}",
            }),
        }
        labels = {
            "icon": "Icone",
            "title_template": "Titulo",
            "body_template": "Corpo",
        }


class TelegramIncomingCommandForm(forms.ModelForm):
    """Form para editar um comando de entrada do Telegram."""

    def clean_command(self):
        command = str(self.cleaned_data.get("command") or "").strip()
        if command and not command.startswith("/"):
            command = f"/{command}"
        if " " in command:
            raise forms.ValidationError("Use apenas o comando base, sem espacos.")
        return command

    class Meta:
        model = TelegramIncomingCommand
        fields = ["command", "enabled", "description"]
        widgets = {
            "command": forms.TextInput(attrs={
                "class": "form-input font-mono text-sm",
                "placeholder": "/replyto",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-input text-sm",
                "rows": 2,
            }),
        }
        labels = {
            "command": "Comando",
            "enabled": "Ativo",
            "description": "Descricao",
        }
