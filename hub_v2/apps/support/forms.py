import uuid
from urllib.parse import urlsplit
from django import forms
from .models import Ticket


class TicketForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    subject = forms.CharField(label='Assunto', max_length=180, min_length=5)
    category = forms.ChoiceField(label='Categoria', choices=Ticket.CATEGORIES)
    body = forms.CharField(label='O que aconteceu?', min_length=10, max_length=8000,
                           widget=forms.Textarea(attrs={'rows': 7}), help_text='Descreva o problema e o que esperava. Não envie senhas, tokens ou códigos de acesso.')
    include_context = forms.BooleanField(label='Compartilhar a página relacionada e a versão do Hub com o atendimento', required=False)
    context_path = forms.CharField(label='Página relacionada (opcional)', max_length=500, required=False,
                                  help_text='Exemplo: /game/. Parâmetros e fragmentos não serão enviados.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('subject', 'body', 'context_path'):
            self.fields[name].widget.attrs['class'] = 'ik-pv'

    def clean_context_path(self):
        value = self.cleaned_data['context_path'].strip()
        if not self.cleaned_data.get('include_context'):
            return ''
        try:
            parts = urlsplit(value)
        except ValueError:
            raise forms.ValidationError('Informe um caminho válido deste Hub.')
        if value and (parts.scheme or parts.netloc or not parts.path.startswith('/') or parts.path.startswith('//') or '\\' in parts.path):
            raise forms.ValidationError('Informe somente um caminho deste Hub, começando por /.')
        return parts.path[:200]


class ReplyForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    body = forms.CharField(label='Sua resposta', min_length=1, max_length=8000, widget=forms.Textarea(attrs={'rows':5, 'class':'ik-pv'}))


class ChangeForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[('reply', 'Responder'), ('take', 'Assumir'), ('release', 'Liberar'), ('status', 'Estado')])
    status = forms.ChoiceField(choices=Ticket.STATES, required=False)
