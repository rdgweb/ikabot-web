"""N-160: the Templos page shows each account's miracle in effect / regenerating, also while the game shows the time
left instead of the miracle's own duration and regeneration."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.temples import attach_miracle_timers, islands_for_organization
from apps.jobs.models import Job, JobLog
from apps.jobs.services.workflows import create_job_with_workflow


def _city(city_id, name, duration, cooldown, read_at):
    return {"id": city_id, "name": name, "island_id": 9, "buildings": [{"building": "temple", "position": 3, "level": 20}],
            "miracle_state": {"wonder_name": "Templo de Poseidon (Nível 3/5)", "activate_state": "hidden", "priests": 1,
                              "max_priests": 10, "duration_text": duration, "cooldown_text": cooldown,
                              "updated_at": read_at.isoformat()}}


class MiracleTimersTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.now = timezone.now()

    def _account(self, name, number, cities):
        ga = GameAccount.objects.create(account=self.account, lobby_account_id=number, server_id="s78-br",
                                        server_language="br", server_number=78, name=name)
        AccountSnapshot.objects.create(account=self.account, game_account=ga, military={}, base_snapshot={}, cities=cities)
        return ga

    def test_in_effect_from_the_reading_and_from_the_activation(self):
        # the first account's temple was read while the miracle was in effect: no duration/regeneration text
        self._account("Ativa", 1, [_city(11, "A", "Ativado por 2h !", "", self.now - timedelta(minutes=30))])
        # the second one was read with the miracle stopped: the miracle's own duration and regeneration
        idle = self._account("Parada", 2, [_city(12, "B", "Duração: 4h", "Regeneração: 12h", self.now)])
        job = create_job_with_workflow(account=self.account, game_account=idle, node=self.node, action_code=11,
                                       inputs={"city": "12", "reason": "envio com 80 de 142 navios"}, status="queued")
        activated = self.now - timedelta(hours=1)
        Job.objects.filter(pk=job.pk).update(status="finished", finished_at=activated)
        log = JobLog.objects.create(job=job, message="Milagre ativado: Templo de Poseidon | efeito=+30%")
        JobLog.objects.filter(pk=log.pk).update(created_at=activated)

        islands = islands_for_organization()
        attach_miracle_timers(islands)
        island = islands[0]
        self.assertEqual((island.duration, island.cooldown), ("Duração: 4h", "Regeneração: 12h"))
        timers = {t["account"]: t for t in island.timers}
        self.assertEqual(set(timers), {"Ativa", "Parada"})
        reading = timers["Ativa"]
        self.assertAlmostEqual((reading["active_until"] - self.now).total_seconds(), 90 * 60, delta=5)
        self.assertAlmostEqual((reading["available_at"] - self.now).total_seconds(), 10.5 * 3600, delta=5)
        by_job = timers["Parada"]
        self.assertAlmostEqual((by_job["active_until"] - self.now).total_seconds(), 3 * 3600, delta=5)
        self.assertAlmostEqual((by_job["available_at"] - self.now).total_seconds(), 11 * 3600, delta=5)
        self.assertEqual(by_job["reason"], "envio grande")

    def test_regenerating_from_the_reading(self):
        self._account("Recarga", 3, [_city(13, "C", "", "O milagre foi ativado recentemente. Tempo restante de regeneração: 2D 15h",
                                           self.now)])
        islands = islands_for_organization()
        attach_miracle_timers(islands)
        timer = islands[0].timers[0]
        self.assertIsNone(timer["active_until"])
        self.assertAlmostEqual((timer["available_at"] - self.now).total_seconds(), (2 * 24 + 15) * 3600, delta=5)
