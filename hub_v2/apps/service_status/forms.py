import uuid
from django import forms
from django.utils import timezone
from .models import Incident


class IncidentForm(forms.ModelForm):
    revision = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    request_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    components = forms.MultipleChoiceField(label='Componentes afetados', choices=Incident.COMPONENTS,
                                           widget=forms.CheckboxSelectMultiple)
    body = forms.CharField(label='Mensagem para os clientes', min_length=10, max_length=5000,
                           widget=forms.Textarea(attrs={'rows': 6}),
                           help_text='Explique o impacto, a orientação e a próxima atualização. Não inclua dados de clientes, IPs internos, senhas ou tokens.')
    confirm_publication = forms.BooleanField(label='Conferi o texto e autorizo sua publicação para todos os usuários do Hub', required=False)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label_suffix','')
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            for name, value in {'kind':'incident','status':'investigating','severity':'degraded','started_at':timezone.now()}.items():
                self.initial.setdefault(name,value)

    class Meta:
        model = Incident
        fields = ['title', 'kind', 'components', 'severity', 'status', 'started_at', 'scheduled_end']
        labels = {'title':'Título', 'kind':'Tipo de aviso', 'severity':'Impacto', 'status':'Situação',
                  'started_at':'Início do incidente ou da manutenção', 'scheduled_end':'Previsão de término (manutenção)'}
        widgets = {name: forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type':'datetime-local'})
                   for name in ('started_at', 'scheduled_end')}

    def clean(self):
        data = super().clean()
        kind, state = data.get('kind'), data.get('status')
        choices = states_for(kind)
        if state and state not in dict(choices):
            self.add_error('status', 'Escolha uma situação compatível com o tipo de aviso.')
        start, end = data.get('started_at'), data.get('scheduled_end')
        if kind == 'maintenance' and not end:
            self.add_error('scheduled_end', 'Informe a previsão de término da manutenção.')
        if end and start and end <= start:
            self.add_error('scheduled_end', 'A previsão deve ser posterior ao início.')
        if start and state not in ('scheduled','cancelled') and start > timezone.now():
            self.add_error('started_at', 'Um evento em andamento não pode começar no futuro.')
        if kind == 'incident' and end:
            self.add_error('scheduled_end', 'A previsão de término é usada apenas para manutenção programada.')
        return data


def states_for(kind):
    allowed = {'scheduled','in_progress','monitoring','resolved','cancelled'} if kind == 'maintenance' else {'investigating','identified','monitoring','resolved'}
    return [(key, label) for key, label in Incident.STATES if key in allowed]


class UpdateForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    status = forms.ChoiceField(label='Nova situação')
    body = forms.CharField(label='Atualização para os clientes', min_length=10, max_length=5000,
                           widget=forms.Textarea(attrs={'rows':5}))
    confirm_publication = forms.BooleanField(label='Conferi o texto e autorizo sua publicação para todos os usuários do Hub')

    def __init__(self, *args, incident, **kwargs):
        kwargs.setdefault('label_suffix','')
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = states_for(incident.kind)
