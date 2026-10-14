"""Fight every finished combat the hub keeps again in the simulator and store the comparison (N-110)."""
from django.core.management.base import BaseCommand

from apps.combat.models import CombatReport
from apps.combat.services.checks import check_report, scoreboard


class Command(BaseCommand):
    help = "Compara o simulador com os combates reais guardados (previsto x real)."

    def handle(self, *args, **options):
        checked = skipped = 0
        for report in CombatReport.objects.filter(finished=True).select_related("game_account"):
            if check_report(report) is None:
                skipped += 1
            else:
                checked += 1
        self.stdout.write(f"comparados: {checked} | sem o que comparar: {skipped} | placar: {scoreboard()}")
