from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.models import Node
from apps.jobs.services.recovery import recover_stale_running_jobs


class Command(BaseCommand):
    help = "Recupera jobs running orfaos que ultrapassaram timeout + grace."

    def add_arguments(self, parser):
        parser.add_argument("--node", dest="node_name", default="", help="Nome do node para filtrar.")

    def handle(self, *args, **options):
        node_name = str(options.get("node_name") or "").strip()
        node = None
        if node_name:
            node = Node.objects.filter(name=node_name).first()
            if not node:
                self.stderr.write(self.style.ERROR(f"Node nao encontrado: {node_name}"))
                return

        result = recover_stale_running_jobs(node=node)
        self.stdout.write(
            self.style.SUCCESS(
                "recovered={recovered} requeued={requeued} marked_error={marked_error}".format(**result)
            )
        )
