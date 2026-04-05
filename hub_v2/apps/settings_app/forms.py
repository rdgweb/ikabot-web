"""
Forms para o app Settings.
"""

from django import forms

from .models import AppSetting


class AppSettingForm(forms.ModelForm):
    class Meta:
        model = AppSetting
        fields = ["key", "value"]
        widgets = {
            "key": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "ex: captcha_solver_url",
            }),
            "value": forms.Textarea(attrs={
                "class": "form-input",
                "rows": 3,
            }),
        }
        labels = {
            "key": "Chave",
            "value": "Valor",
        }


class WebshareSettingsForm(forms.Form):
    api_key = forms.CharField(
        label="Chave API Webshare",
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-input",
            "autocomplete": "off",
            "placeholder": "Token da API Webshare.io",
        }),
        help_text="Deixe em branco para manter o valor atual.",
    )


class IkabotApiSettingsForm(forms.Form):
    url = forms.URLField(
        label="URL do ikabotapi",
        required=False,
        widget=forms.URLInput(attrs={
            "class": "form-input",
            "placeholder": "http://ikabotapi:5005",
        }),
        help_text="Endereco do servico de captcha solver.",
    )


class AgentSecurityForm(forms.Form):
    allowed_ips = forms.CharField(
        label="IPs permitidos",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-input",
            "placeholder": "ex: 192.168.1.0/24, 10.0.0.5",
        }),
        help_text="IPs ou CIDRs separados por virgula. Vazio = aceitar qualquer IP.",
    )


class SnapshotPolicyForm(forms.Form):
    snapshot_stale_seconds = forms.IntegerField(
        label="Snapshot geral vencido apos (segundos)",
        min_value=60,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "form-input",
            "placeholder": "7200",
        }),
        help_text="Usado por runners que dependem do snapshot geral da conta/cidades.",
    )
    building_options_stale_seconds = forms.IntegerField(
        label="Opcoes de edificios vencidas apos (segundos)",
        min_value=60,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "form-input",
            "placeholder": "21600",
        }),
        help_text="Quando vencer, o Check Status agenda o sync 1001 para atualizar os predios construiveis.",
    )
    running_job_recovery_grace_seconds = forms.IntegerField(
        label="Buffer para recuperar job travado (segundos)",
        min_value=60,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "form-input",
            "placeholder": "300",
        }),
        help_text="Se um job ficar running por mais que timeout + este buffer, o hub encerra a execucao orfa e tenta requeue nas acoes seguras.",
    )
    running_job_lease_seconds = forms.IntegerField(
        label="Lease do job em execucao (segundos)",
        min_value=30,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "form-input",
            "placeholder": "180",
        }),
        help_text="Cada worker renova esse lease enquanto o job esta vivo. Se o lease expirar, o job entra no recovery.",
    )
