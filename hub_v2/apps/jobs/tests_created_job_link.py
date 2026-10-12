"""N-100: after creating a job, "Ver job" opens that job, not the whole queue."""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.jobs.services.workflows import collect_created_jobs, create_job_with_workflow


def _building(position, building, level):
    return {"position": position, "building": building, "level": level, "is_upgrading": False}


class CreatedJobLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="link-tester", email="link@example.com", password="secret123")
        self.node = Node.objects.create(name="agent-link")
        self.account = Account.objects.create(node=self.node, label="Lobby Link", email="link-lobby@example.com", password_enc="enc")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=778, server_id="s9-br", server_language="br", server_number=9, name="LinkPlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={}, military={},
            cities=[
                {"id": "501", "name": "Alfa", "tradegood": 1, "buildings": [_building(0, "townHall", 10), _building(1, "warehouse", 5)]},
                {"id": "502", "name": "Beta", "tradegood": 2, "buildings": [_building(0, "townHall", 8), _building(4, "tavern", 3)]},
            ],
        )
        self.client.force_login(self.user)

    def _demolish(self, items):
        return self.client.post(reverse("jobs:job-submit"), {
            "game_account": str(self.ga.pk), "action_code": "1303", "demolish_items_json": json.dumps(items),
            "demolish_acknowledge": "on", "demolish_confirm_word": "DEMOLIR",
        })

    @staticmethod
    def _item(city_id, position, building, level):
        return {"city_id": city_id, "position": position, "building": building, "level": level, "mode": "levels", "levels": 1}

    def test_one_job_created_links_to_that_job(self):
        response = self._demolish([self._item("501", 1, "warehouse", 5)])
        page = response.content.decode()

        job = Job.objects.get(game_account=self.ga, action_code=1303)
        self.assertIn(f'hx-get="{reverse("jobs:job-detail", args=[job.pk])}"', page)
        self.assertIn("Ver job\n", page)
        self.assertNotIn(f'hx-get="{reverse("jobs:job-list")}"', page)
        # and that page opens
        self.assertEqual(self.client.get(reverse("jobs:job-detail", args=[job.pk])).status_code, 200)

    def test_several_jobs_created_link_to_the_queue_filtered_to_them(self):
        response = self._demolish([self._item("501", 1, "warehouse", 5), self._item("502", 4, "tavern", 3)])
        page = response.content.decode()

        jobs = list(Job.objects.filter(game_account=self.ga, action_code=1303))
        self.assertEqual(len(jobs), 2)
        self.assertIn("Ver os 2 jobs", page)
        link = page.split('hx-get="')[1].split('"')[0].replace("&amp;", "&")
        self.assertTrue(link.startswith(reverse("jobs:job-list") + "?"), link)
        self.assertIn(f"game_account={self.ga.pk}", link)
        self.assertIn(f"workflow_type={jobs[0].workflow.workflow_type}", link)
        self.assertEqual(self.client.get(link).status_code, 200)

    def test_a_refused_submission_shows_no_success_step(self):
        response = self.client.post(reverse("jobs:job-submit"), {"game_account": str(self.ga.pk), "action_code": "1303"})
        self.assertNotIn("Ver job", response.content.decode())
        self.assertFalse(Job.objects.filter(game_account=self.ga).exists())

    def test_the_collector_sees_only_the_jobs_of_its_block(self):
        def make():
            return create_job_with_workflow(
                account=self.account, game_account=self.ga, node=self.node, action_code=100, inputs={}, status="queued",
            )

        before = make()
        with collect_created_jobs() as created:
            first = make()
            with collect_created_jobs() as inner:
                nested = make()
            second = make()
        after = make()

        self.assertEqual([job.pk for job in created], [first.pk, second.pk])
        self.assertEqual([job.pk for job in inner], [nested.pk])
        self.assertNotIn(before.pk, [job.pk for job in created])
        self.assertNotIn(after.pk, [job.pk for job in created])

    def test_without_any_job_the_button_still_opens_the_queue(self):
        from django.template.loader import render_to_string

        page = render_to_string("jobs/partials/create_step_success.html", {"jobs_created": 0, "action_name": "Teste"})
        self.assertIn(f'hx-get="{reverse("jobs:job-list")}"', page)
        self.assertIn("Ver jobs\n", page)
