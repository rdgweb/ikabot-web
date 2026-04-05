"""
Forms for Profile CRUD.
"""

from django import forms

from .models import Profile


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["name", "description", "action_code", "inputs_json", "timeout_sec", "enabled"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "action_code": forms.NumberInput(attrs={"class": "form-input"}),
            "inputs_json": forms.Textarea(attrs={"class": "form-input font-mono text-sm", "rows": 4, "placeholder": "[]"}),
            "timeout_sec": forms.NumberInput(attrs={"class": "form-input"}),
        }
