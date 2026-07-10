from django import forms

from .models import ChangeLogEntry, Note, NoteEvent


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ["title", "note_type", "priority", "status", "source_url", "tags", "body"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input", "placeholder": "Resumo curto"}),
            "note_type": forms.Select(attrs={"class": "form-input"}),
            "priority": forms.Select(attrs={"class": "form-input"}),
            "status": forms.Select(attrs={"class": "form-input"}),
            "source_url": forms.URLInput(attrs={"class": "form-input", "placeholder": "URL relacionada, se tiver"}),
            "tags": forms.TextInput(attrs={"class": "form-input", "placeholder": "mercado, treino, snapshot"}),
            "body": forms.Textarea(attrs={"class": "form-input", "rows": 7, "placeholder": "Detalhes, contexto, passos para reproduzir ou checklist"}),
        }


class ChangeLogEntryForm(forms.ModelForm):
    class Meta:
        model = ChangeLogEntry
        fields = ["visibility", "component", "version", "dev_version", "published_version", "title", "note", "body"]
        widgets = {
            "visibility": forms.Select(attrs={"class": "form-input"}),
            "component": forms.Select(attrs={"class": "form-input"}),
            "version": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: 0.0.96"}),
            "dev_version": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: 0.2.42-dev"}),
            "published_version": forms.TextInput(attrs={"class": "form-input", "placeholder": "ex: 0.3.0"}),
            "title": forms.TextInput(attrs={"class": "form-input", "placeholder": "O que mudou"}),
            "note": forms.Select(attrs={"class": "form-input"}),
            "body": forms.Textarea(attrs={"class": "form-input", "rows": 6, "placeholder": "Resumo da alteracao, validacao e impacto"}),
        }


class NoteEventForm(forms.ModelForm):
    class Meta:
        model = NoteEvent
        fields = ["event_type", "message"]
        widgets = {
            "event_type": forms.Select(attrs={"class": "form-input"}),
            "message": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "Atualizacao curta do andamento"}),
        }
