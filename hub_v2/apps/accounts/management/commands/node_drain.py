"""N-261: pause new jobs on a node while it is being updated by the platform's deploy.

    manage.py node_drain <node-name-or-id> on [--seconds N]   -> prints the jobs still running
    manage.py node_drain <node-name-or-id> status              -> prints the jobs still running
    manage.py node_drain <node-name-or-id> off
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import node_drain
from apps.accounts.models import Node


class Command(BaseCommand):
    help = "Pausa jobs novos de um nó enquanto ele é atualizado (N-261)."

    def add_arguments(self, parser):
        parser.add_argument("node")
        parser.add_argument("action", choices=["on", "off", "status"])
        parser.add_argument("--seconds", type=int, default=3600)

    def handle(self, *args, **options):
        ref = options["node"]
        node = Node._base_manager.filter(name=ref).first()
        if node is None:
            try:
                node = Node._base_manager.filter(pk=ref).first()
            except Exception:
                node = None
        if node is None:
            raise CommandError("no não encontrado")
        if options["action"] == "on":
            node_drain.start_drain(node, options["seconds"])
        elif options["action"] == "off":
            node_drain.stop_drain(node)
        self.stdout.write(str(node_drain.busy_jobs(node)))
