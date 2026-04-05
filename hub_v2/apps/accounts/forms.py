"""
Forms for Node and Account CRUD.
"""

from django import forms
from django.db import models

from core.encryption import encrypt, decrypt
from apps.proxy.models import ProxyProfile
from .models import Node, Account


# ── Node Forms ──

class _NodeFormBase(forms.ModelForm):
    """Shared logic for Node create/edit — proxy select + auto-sync."""

    proxy_profile = forms.ModelChoiceField(
        queryset=ProxyProfile.objects.filter(active=True),
        required=False,
        label="Proxy",
        empty_label="— Sem proxy —",
        widget=forms.Select(attrs={"class": "form-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Pre-select the currently assigned proxy
            try:
                self.fields["proxy_profile"].initial = self.instance.proxy_profile
            except ProxyProfile.DoesNotExist:
                pass
        # Show unassigned proxies + the one currently assigned to this node
        qs = ProxyProfile.objects.filter(active=True).filter(
            models.Q(assigned_node__isnull=True) | models.Q(assigned_node=self.instance)
        )
        self.fields["proxy_profile"].queryset = qs

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_profile = self.cleaned_data.get("proxy_profile")

        # Get old assignment
        old_profile = None
        if instance.pk:
            try:
                old_profile = instance.proxy_profile
            except ProxyProfile.DoesNotExist:
                pass

        if commit:
            # Update Node.proxy from profile
            instance.proxy = new_profile.proxy_url if new_profile else ""
            instance.save()

            # Unassign old proxy if changed
            if old_profile and old_profile != new_profile:
                old_profile.assigned_node = None
                old_profile.save(update_fields=["assigned_node"])

            # Assign new proxy
            if new_profile and new_profile.assigned_node != instance:
                new_profile.assigned_node = instance
                new_profile.save(update_fields=["assigned_node"])
            elif not new_profile and old_profile:
                # Cleared proxy — already unassigned above
                pass

        return instance


class NodeCreateForm(_NodeFormBase):
    class Meta:
        model = Node
        fields = ["name", "description", "location", "external_ip"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: vps-us-east-1"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "location": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: São Paulo, BR"}),
            "external_ip": forms.TextInput(attrs={"class": "form-input", "placeholder": "IP esperado do agent"}),
        }


class NodeEditForm(_NodeFormBase):
    class Meta:
        model = Node
        fields = ["name", "description", "location", "external_ip", "active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "location": forms.TextInput(attrs={"class": "form-input"}),
            "external_ip": forms.TextInput(attrs={"class": "form-input"}),
        }


# ── Account (Lobby) Forms ──

class AccountCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-input", "autocomplete": "new-password"}),
        help_text="Senha do GameForge. Será armazenada com criptografia Fernet.",
    )
    gf_token = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2, "placeholder": "gf-token-production cookie"}),
        help_text="Token gf-token-production (opcional). Será criptografado.",
    )

    class Meta:
        model = Account
        fields = ["node", "label", "email", "password", "gf_token", "notes"]
        widgets = {
            "node": forms.Select(attrs={"class": "form-input"}),
            "label": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: Conta Principal"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "notes": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.password_enc = encrypt(self.cleaned_data["password"])
        gf_token = self.cleaned_data.get("gf_token", "")
        if gf_token:
            instance.gf_token_enc = encrypt(gf_token)
        if commit:
            instance.save()
        return instance


class AccountEditForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-input", "autocomplete": "new-password"}),
        help_text="Deixe em branco para manter a senha atual.",
    )
    gf_token = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
        help_text="Deixe em branco para manter o token atual. Será criptografado.",
    )

    class Meta:
        model = Account
        fields = ["node", "label", "email", "password", "gf_token", "notes", "active"]
        widgets = {
            "node": forms.Select(attrs={"class": "form-input"}),
            "label": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "notes": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_password = self.cleaned_data.get("password")
        if new_password:
            instance.password_enc = encrypt(new_password)
        gf_token = self.cleaned_data.get("gf_token", "")
        if gf_token:
            instance.gf_token_enc = encrypt(gf_token)
        if commit:
            instance.save()
        return instance
