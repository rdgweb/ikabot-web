from django import forms
from django.contrib.auth.models import User
from apps.orgs.models import Membership, Organization


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"class": "form-input"}))
    organization = forms.ModelChoiceField(queryset=Organization.objects.all(), label="Organização", widget=forms.Select(attrs={"class": "form-input"}))
    role = forms.ChoiceField(choices=Membership.ROLE_CHOICES, label="Papel na organização", widget=forms.Select(attrs={"class": "form-input"}))

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "password", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "first_name": forms.TextInput(attrs={"class": "form-input"}),
            "last_name": forms.TextInput(attrs={"class": "form-input"}),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user._own_organization = True
            user.save()
            Membership.objects.create(user=user, organization=self.cleaned_data["organization"], role=self.cleaned_data["role"])
        return user


class UserEditForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-input"}),
        help_text="Deixe em branco para manter a senha atual.",
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "password", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "first_name": forms.TextInput(attrs={"class": "form-input"}),
            "last_name": forms.TextInput(attrs={"class": "form-input"}),
        }

    def clean_is_active(self):
        active = self.cleaned_data["is_active"]
        if self.instance.is_superuser and not active:
            raise forms.ValidationError("O administrador principal não pode ser desativado nesta tela.")
        return active

    def save(self, commit=True):
        user = super().save(commit=False)
        new_pw = self.cleaned_data.get("password")
        if new_pw:
            user.set_password(new_pw)
        if commit:
            user.save()
        return user
