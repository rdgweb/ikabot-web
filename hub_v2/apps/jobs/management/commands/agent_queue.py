"""Inspeciona e limpa as filas Celery/Redis dos nós (agents). Ver tambem a UI em
Nós & Agents > detalhe do nó.

Uso:
    python manage.py agent_queue                      # lista filas + tamanhos
    python manage.py agent_queue --purge <node> --yes # purga fila+unacked do no
    python manage.py agent_queue --purge-all --yes

Depois de purgar, REINICIE o worker daquele no (docker restart) para soltar as
tarefas que ele ja tinha em memoria.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.models import Node
from apps.jobs.services.agent_queue import all_queue_stats, purge_node


class Command(BaseCommand):
    help = "Inspeciona/purga as filas Celery (Redis) dos nós agents."

    def add_arguments(self, parser):
        parser.add_argument("--purge", metavar="NODE_ID", help="Purga fila+unacked deste nó.")
        parser.add_argument("--purge-all", action="store_true", help="Purga fila+unacked de TODOS os nós.")
        parser.add_argument("--yes", action="store_true", help="Confirma a purga sem prompt.")

    def handle(self, *args, **opts):
        nodes = list(Node.objects.all())
        node_ids = [str(n.pk) for n in nodes]
        label_of = {str(n.pk): (getattr(n, "label", "") or getattr(n, "name", "") or str(n.pk)) for n in nodes}
        stats = all_queue_stats(node_ids)

        purge = opts.get("purge")
        purge_all = opts.get("purge_all")

        if purge or purge_all:
            targets = node_ids if purge_all else [str(purge)]
            est = sum(stats.get(k, {}).get("queue_len", 0) + stats.get(k, {}).get("unacked", 0) for k in targets)
            if not opts.get("yes"):
                self.stdout.write(self.style.WARNING(
                    f"Vai purgar {len(targets)} nó(s), ~{est} mensagem(ns). Rode com --yes para confirmar."
                ))
                return
            tl = tu = 0
            for k in targets:
                res = purge_node(k)
                tl += res["queue_len"]
                tu += res["unacked"]
                if res["queue_len"] or res["unacked"]:
                    self.stdout.write(self.style.SUCCESS(
                        f"{label_of.get(k, k)}: fila={res['queue_len']} unacked={res['unacked']} removidas."
                    ))
            self.stdout.write(self.style.SUCCESS(f"Total: fila={tl} unacked={tu}."))
            self.stdout.write(self.style.WARNING("Agora REINICIE o worker do(s) nó(s) (docker restart)."))
            return

        self.stdout.write(self.style.HTTP_INFO("Filas Celery por nó (Redis):"))
        gl = gu = 0
        for k in node_ids:
            s = stats.get(k, {"queue_len": 0, "unacked": 0})
            gl += s["queue_len"]
            gu += s["unacked"]
            state = self.style.ERROR(f"fila={s['queue_len']} unacked={s['unacked']}") if (s["queue_len"] or s["unacked"]) else "vazia"
            self.stdout.write(f"  {label_of.get(k, k)} ({k}): {state}")
        self.stdout.write(f"Total fila imediata: {gl} | unacked de nós: {gu}")
        if gl or gu:
            self.stdout.write(self.style.WARNING(
                "Limpar: python manage.py agent_queue --purge <node_id> --yes  (depois docker restart do worker)"
            ))
