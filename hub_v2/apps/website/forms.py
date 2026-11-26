from django import forms

from apps.billing import plans


class CostEstimateForm(forms.Form):
    active_subaccounts = forms.IntegerField(min_value=1, max_value=plans.ABOVE_MAX, initial=4)
    lobbies = forms.IntegerField(min_value=1, max_value=plans.ABOVE_MAX, initial=1)
    platform_agents = forms.IntegerField(min_value=0, max_value=plans.ABOVE_MAX, initial=0)
    platform_proxies = forms.IntegerField(min_value=0, max_value=plans.ABOVE_MAX, initial=0)

    def __init__(self, *args, lang='pt', **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = lang
        labels = (('Personagens ativos (subcontas)', 'Contas GameForge (lobbies)',
                   'Lobbies com agente da plataforma', 'Lobbies com pacote de proxies da plataforma') if lang == 'pt' else
                  ('Active characters (sub-accounts)', 'GameForge accounts (lobbies)',
                   'Lobbies with a platform agent', 'Lobbies with a platform proxy package'))
        for field, label in zip(self.fields.values(), labels):
            field.label = label
            field.error_messages.update(({
                'required': 'Informe uma quantidade.', 'invalid': 'Informe um número inteiro.',
                'min_value': 'Informe pelo menos %(limit_value)s.', 'max_value': 'Informe no máximo %(limit_value)s.',
            } if lang == 'pt' else {
                'required': 'Enter a count.', 'invalid': 'Enter a whole number.',
                'min_value': 'Enter at least %(limit_value)s.', 'max_value': 'Enter at most %(limit_value)s.',
            }))

    def clean(self):
        data = super().clean()
        lobbies = data.get('lobbies')
        if lobbies is not None:
            for name in ('platform_agents', 'platform_proxies'):
                if data.get(name, 0) > lobbies:
                    self.add_error(name, 'A quantidade não pode exceder os lobbies.' if self.lang == 'pt' else 'Count cannot exceed lobbies.')
        return data
