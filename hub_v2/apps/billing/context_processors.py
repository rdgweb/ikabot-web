"""The plan's notice at the top of the panel (N-124): trial ending, payment late, work paused."""
from datetime import timedelta

from django.utils import timezone


def billing_notice(request):
    user = getattr(request, "user", None)
    organization = getattr(request, "organization", None)
    if not getattr(user, "is_authenticated", False) or organization is None or request.path.startswith("/organizacao/plano"):
        return {}
    from .services import state_for

    state = state_for(organization)
    if state.code in ("exempt", "active"):
        return {}
    if state.code == "trial":
        left = state.ends_at - timezone.now() if state.ends_at else timedelta(days=99)
        if left > timedelta(days=3):
            return {}
        text = f"Seu teste grátis termina em {max(1, left.days + (1 if left.seconds else 0))} dia(s). Assine para os jobs não pararem."
        level = "warn"
    elif state.code == "overdue":
        text = "O pagamento do plano venceu. Os jobs pausam em até 3 dias se ele não for pago."
        level = "warn"
    elif state.code == "paused":
        text = f"{state.label}: os agentes não recebem jobs. Nada foi apagado — assine ou pague e tudo volta na hora."
        level = "bad"
    else:
        text = "Organização só para leitura por falta de pagamento. Regularize o plano para voltar a usar."
        level = "bad"
    return {"BILLING_NOTICE": {"text": text, "level": level}}
