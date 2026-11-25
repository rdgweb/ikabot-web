from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from .account_services import normalized_email, normalize_avatar


class ProfileForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="Nome")
    last_name = forms.CharField(max_length=150, required=False, label="Sobrenome")
    display_name = forms.CharField(max_length=80, required=False, label="Nome de exibição")
    avatar = forms.FileField(required=False, label="Foto")
    remove_avatar = forms.BooleanField(required=False, label="Remover foto atual")

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        return normalize_avatar(avatar) if avatar else None

    def clean(self):
        data = super().clean()
        if data.get("avatar") and data.get("remove_avatar"):
            raise ValidationError("Escolha uma foto nova ou remova a atual, uma opção por vez.")
        return data


class EmailChangeForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Novo e-mail")
    current_password = forms.CharField(widget=forms.PasswordInput, label="Senha atual")

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        return normalized_email(self.cleaned_data["email"])

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not get_user_model().objects.get(pk=self.user.pk).check_password(password):
            raise ValidationError("Senha atual incorreta.")
        return password
