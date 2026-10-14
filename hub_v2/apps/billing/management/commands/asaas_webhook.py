"""Register the hub's webhook in the Asaas account of ASAAS_API_KEY (N-124). Prints no token or key."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing import asaas


class Command(BaseCommand):
    help = "Cadastra no Asaas o webhook do hub (usa ASAAS_API_KEY e ASAAS_WEBHOOK_TOKEN)."

    def add_arguments(self, parser):
        parser.add_argument("--url", default="", help="endereço público do hub (padrão: SITE_URL ou HUB_PUBLIC_URL)")
        parser.add_argument("--email", default="", help="e-mail que o Asaas avisa se o webhook falhar")

    def handle(self, *args, **options):
        base = (options["url"] or settings.SITE_URL or settings.HUB_PUBLIC_URL).rstrip("/")
        email = options["email"] or settings.SITE_LEGAL.get("email", "")
        if not base.startswith("https://"):
            raise CommandError("O webhook precisa de um endereço https público (--url).")
        if not settings.ASAAS_WEBHOOK_TOKEN or len(settings.ASAAS_WEBHOOK_TOKEN) < 32:
            raise CommandError("Defina ASAAS_WEBHOOK_TOKEN no .env (32 caracteres ou mais).")
        if not email:
            raise CommandError("Informe --email ou SITE_CONTACT_EMAIL.")
        try:
            webhook_id = asaas.create_webhook(url=f"{base}/api/billing/asaas/", token=settings.ASAAS_WEBHOOK_TOKEN, email=email)
        except asaas.AsaasError as exc:
            raise CommandError(str(exc)) from exc
        mode = "teste (sandbox)" if settings.ASAAS_SANDBOX else "produção"
        self.stdout.write(self.style.SUCCESS(f"Webhook cadastrado no Asaas ({mode}): {webhook_id or 'ok'} -> {base}/api/billing/asaas/"))
