"""Fight every finished combat the hub keeps again in the simulator and store the comparison (N-110).

Also keeps what each report shows about a wall and about the upgrades of who fought (N-112),
and fights again the combats read from outside, whose inputs are stored.
"""
from django.core.management.base import BaseCommand

from apps.combat.models import BattleIntel, CombatReport, SimulationCheck
from apps.combat.services.checks import check_report, recheck_sample, scoreboard
from apps.combat.services.intel import learn_safely
from apps.combat.services.report_parser import parse_rounds


class Command(BaseCommand):
    help = "Compara o simulador com os combates reais guardados (previsto x real)."

    def handle(self, *args, **options):
        checked = skipped = 0
        for report in CombatReport.objects.select_related("game_account"):
            learn_safely(report, parse_rounds(report.detailed_html))
            if not report.finished or check_report(report) is None:
                skipped += 1
            else:
                checked += 1
        outside = 0
        for check in SimulationCheck.objects.filter(report__isnull=True):
            recheck_sample(check)
            outside += 1
        self.stdout.write(
            f"comparados: {checked} | de fora: {outside} | sem o que comparar: {skipped} | "
            f"muralhas e melhorias conhecidas: {BattleIntel.objects.count()} | placar: {scoreboard()}"
        )
