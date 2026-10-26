"""N-163: apply the validity by mission to the spy reports already saved (counted from when each was saved)."""
from django.core.management.base import BaseCommand

from apps.espionage.models import SpyReport
from apps.espionage.services.validity import expires_at_for


class Command(BaseCommand):
    help = "Recalcula a validade dos relatórios de espionagem pela regra por missão (N-163)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Só conta, não grava.")

    def handle(self, *args, dry_run=False, **options):
        changed = 0
        total = 0
        for report in SpyReport.objects.all().only("pk", "created_at", "mission_id", "data_json", "result_status", "expires_at").iterator():
            total += 1
            expiry = expires_at_for(report)
            if report.expires_at != expiry:
                changed += 1
                if not dry_run:
                    SpyReport.objects.filter(pk=report.pk).update(expires_at=expiry)
        self.stdout.write(f"{total} relatórios, {changed} com validade {'a mudar' if dry_run else 'atualizada'}")
