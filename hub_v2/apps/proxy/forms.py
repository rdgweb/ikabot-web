"""
Forms para o app Proxy — CRUD de proxy manual e Webshare.
"""

from django import forms

from core.encryption import encrypt
from .models import ProxyProfile


class ProxyForm(forms.ModelForm):
    """Create/edit form for proxy profiles."""

    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-input", "autocomplete": "new-password"}),
        label="Senha",
        help_text="Deixe em branco para manter a senha atual (edição).",
    )

    class Meta:
        model = ProxyProfile
        fields = [
            "address", "port", "username", "password",
            "country_code", "provider", "assigned_node", "active",
        ]
        widgets = {
            "address": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: p.webshare.io"}),
            "port": forms.NumberInput(attrs={"class": "form-input", "placeholder": "ex: 9090"}),
            "username": forms.TextInput(attrs={"class": "form-input", "placeholder": "usuario (opcional)"}),
            "country_code": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: BR"}),
            "provider": forms.Select(attrs={"class": "form-input"}),
            "assigned_node": forms.Select(attrs={"class": "form-input"}),
        }
        labels = {
            "address": "Endereço",
            "port": "Porta",
            "username": "Usuário",
            "country_code": "País (código)",
            "provider": "Provedor",
            "assigned_node": "Nó atribuído",
            "active": "Ativo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_node"].required = False
        self.fields["assigned_node"].empty_label = "— Nenhum (livre) —"

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Encrypt password if provided
        new_password = self.cleaned_data.get("password")
        if new_password:
            instance.password_enc = encrypt(new_password)

        # Track old assigned node to clear Node.proxy if unassigned
        old_node = None
        if instance.pk:
            try:
                old_instance = ProxyProfile.objects.get(pk=instance.pk)
                old_node = old_instance.assigned_node
            except ProxyProfile.DoesNotExist:
                pass

        if commit:
            instance.save()

            # Sync Node.proxy when assignment changes
            new_node = instance.assigned_node
            if old_node and old_node != new_node:
                # Clear proxy from previously assigned node
                old_node.proxy = ""
                old_node.save(update_fields=["proxy"])

            if new_node:
                # Set proxy URL on newly assigned node
                new_node.proxy = instance.proxy_url
                new_node.save(update_fields=["proxy"])

        return instance
