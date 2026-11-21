"""The plan's notice at the top of the panel (N-124): trial ending, payment late, work paused."""
from datetime import timedelta

from django.utils import timezone


def billing_flags(request):
    """N-208: BILLING_OPEN — may this person see the plan and subscribe (lazy: only pages that ask pay the query)."""
    user = getattr(request, "user", None)

    def is_open():
        from .services import billing_open

        return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False) or billing_open())

    return {"BILLING_OPEN": is_open}


def billing_notice(request):
    user = getattr(request, "user", None)
    organization = getattr(request, "organization", None)
    if not getattr(user, "is_authenticated", False) or organization is None or request.path.startswith("/organizacao/plano"):
        return {}
    from .services import billing_open, state_for

    is_open = billing_open() or getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    state = state_for(organization)
    if state.code in ("exempt", "active"):
        return {}
    if state.code == "trial":
        left = state.ends_at - timezone.now() if state.ends_at else timedelta(days=99)
        if left > timedelta(days=3):
            return {}
        days = max(1, left.days + (1 if left.seconds else 0))
        text = (f"Seu teste grátis termina em {days} dia(s). Assine para os jobs não pararem." if is_open
                else f"Seu acesso de teste vai até {timezone.localtime(state.ends_at):%d/%m}." if state.ends_at else "")
        level = "warn"
    elif state.code == "overdue":
        text = "O pagamento do plano venceu. Os jobs pausam em até 3 dias se ele não for pago."
        level = "warn"
    elif state.code == "paused":
        text = (f"{state.label}: os agentes não recebem jobs. Nada foi apagado — assine ou pague e tudo volta na hora." if is_open
                else f"{state.label}: os agentes não recebem jobs. Nada foi apagado — fale com o administrador.")
        level = "bad"
    else:
        text = ("Organização só para leitura por falta de pagamento. Regularize o plano para voltar a usar." if is_open
                else "Seu acesso de teste terminou: a organização ficou só para leitura. Fale com o administrador.")
        level = "bad"
    if not text:
        return {}
    return {"BILLING_NOTICE": {"text": text, "level": level, "open": is_open}}
