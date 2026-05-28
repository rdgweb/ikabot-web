"""
Backfill SpyReport.target_owner_id a partir dos dados do WorldDump.

Relatórios criados antes do campo target_owner_id existir ficaram com valor "".
Este comando cruza target_city_id com WorldDumpCity.game_city_id para preencher.

Uso:
    python manage.py backfill_spy_owner_id
    python manage.py backfill_spy_owner_id --dry-run
    python manage.py backfill_spy_owner_id --batch-size 500
"""

from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery

from apps.espionage.models import SpyReport
from apps.worldintel.models import WorldDumpCity


class Command(BaseCommand):
    help = "Preenche target_owner_id nos SpyReport antigos usando dados do WorldDump."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra quantos registros seriam atualizados, sem salvar.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Número de registros processados por batch (default: 500).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        qs = SpyReport.objects.filter(
            target_owner_id="",
            target_city_id__gt="",
        )
        total = qs.count()
        self.stdout.write(f"Relatórios sem owner_id: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nada a fazer."))
            return

        # Subquery: owner_id mais recente para este game_city_id
        city_owner_sq = (
            WorldDumpCity.objects
            .filter(game_city_id=OuterRef("target_city_id"))
            .exclude(owner_id="")
            .order_by("-dump__captured_at")
            .values("owner_id")[:1]
        )

        updated = 0
        offset = 0

        while True:
            batch_ids = list(
                qs.values_list("pk", flat=True)[offset : offset + batch_size]
            )
            if not batch_ids:
                break

            rows = SpyReport.objects.filter(pk__in=batch_ids).annotate(
                found_owner_id=Subquery(city_owner_sq)
            )

            to_update = []
            for row in rows:
                if row.found_owner_id:
                    row.target_owner_id = row.found_owner_id
                    to_update.append(row)

            if to_update and not dry_run:
                SpyReport.objects.bulk_update(to_update, ["target_owner_id"], batch_size=200)

            updated += len(to_update)
            offset += batch_size
            self.stdout.write(f"  Processados {min(offset, total)}/{total} — preenchidos {updated}")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[dry-run] Seriam preenchidos: {updated}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Concluído. {updated} relatórios atualizados."))
