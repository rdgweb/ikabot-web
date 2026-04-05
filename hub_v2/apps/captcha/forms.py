"""
Forms para o app Captcha.
"""

from django import forms

from .models import CaptchaConfig


class CaptchaConfigForm(forms.ModelForm):
    """Form para configuracao da estrategia de captcha."""

    class Meta:
        model = CaptchaConfig
        fields = ["enabled", "solve_method", "fallback_method", "ikabotapi_url", "timeout_sec"]
        widgets = {
            "solve_method": forms.Select(attrs={"class": "form-input"}),
            "fallback_method": forms.Select(attrs={"class": "form-input"}),
            "ikabotapi_url": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "http://ikabotapi:5100",
            }),
            "timeout_sec": forms.NumberInput(attrs={
                "class": "form-input w-24",
                "min": 30,
                "max": 600,
            }),
        }
        labels = {
            "enabled": "Captcha ativo",
            "solve_method": "Metodo principal",
            "fallback_method": "Metodo alternativo",
            "ikabotapi_url": "URL do ikabotapi",
            "timeout_sec": "Timeout (seg)",
        }


class CaptchaSolveForm(forms.Form):
    """Form para resolver captcha manualmente."""

    solution = forms.CharField(
        max_length=512,
        widget=forms.TextInput(attrs={
            "class": "form-input text-lg font-mono text-center tracking-widest",
            "placeholder": "Digite a resposta...",
            "autofocus": True,
        }),
        label="Resposta",
    )
