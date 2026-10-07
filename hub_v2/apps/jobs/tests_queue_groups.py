"""N-88: the job queue grouped before pagination, and the action menus as subjects."""

import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from core.actions import ACTION_CATALOG
from core.actions.constants import CATEGORY_META, CATEGORY_ORDER

from .models import Job, Workflow
from .services.workflows import ensure_workflow_for_job, workflow_type_info

DISTRIBUTE, LOGIN, WINE_ALERT, PLAN = 3, 6, 702, 1002
migration = importlib.import_module("apps.jobs.migrations.0015_workflow_categories_by_subject")


class QueueGroupingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="queue", email="queue@example.com", password="secret123")
        self.gas = {}
        # created out of alphabetical order on purpose
        for position, name in enumerate(("Zeta", "Alfa", "Meio")):
            node = Node.objects.create(name=f"node-{name}")
            account = Account.objects.create(node=node, label=f"Lobby {name}", email=f"{name}@example.com", password_enc="x")
            self.gas[name] = GameAccount.objects.create(
                account=account, lobby_account_id=position + 1, server_id="s1-br", server_language="br",
                server_number=1, name=name,
            )
        # interleaved, as the queue fills up in real use
        for code in (WINE_ALERT, PLAN, DISTRIBUTE, LOGIN):
            for name in ("Zeta", "Alfa", "Meio"):
                self._workflow(name, code)
        self.client.force_login(self.user)

    def _workflow(self, name, code):
        ga = self.gas[name]
        job = Job.objects.create(
            account=ga.account, game_account=ga, node=ga.account.node, action_code=code,
            status="finished", inputs_json="{}", timeout_sec=1800,
        )
        return ensure_workflow_for_job(job)

    def _page(self, **params):
        response = self.client.get(reverse("jobs:job-list"), params)
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def _accounts(sub):
        return [row["workflow"].game_account.name for row in sub["rows"]]

    def test_new_workflows_get_the_subject_menus(self):
        stored = dict(Workflow.objects.values_list("workflow_type", "category"))
        self.assertEqual(
            (stored["distribute"], stored["login_daily"], stored["alert_wine"], stored["construction_plan"]),
            ("resources", "account", "monitoring", "construction"),
        )

    def test_a_small_menu_is_never_split_by_the_page_break(self):
        # 12 workflows, 5 per page: before, the 3 construction plans were spread over the pages
        response = self._page(per_page=5)

        groups = response.context["workflow_groups"]
        self.assertEqual([(g["key"], g["shown"], g["total"]) for g in groups], [("construction", 3, 3), ("resources", 2, 3)])
        self.assertIn("2 de 3 nesta pagina", response.content.decode())
        second = self._page(per_page=5, page=2).context["workflow_groups"]
        self.assertEqual([(g["key"], g["shown"], g["total"]) for g in second], [("resources", 1, 3), ("monitoring", 3, 3), ("account", 1, 3)])

    def test_menu_then_action_with_rows_ordered_by_account(self):
        groups = self._page().context["workflow_groups"]

        self.assertEqual([g["key"] for g in groups], ["construction", "resources", "monitoring", "account"])
        self.assertEqual([g["label"] for g in groups], ["Construcao", "Recursos e logistica", "Alertas e monitoramento", "Conta"])
        resources = groups[1]
        self.assertEqual([(s["label"], s["shown"], s["total"]) for s in resources["subgroups"]], [("Distribuir Recursos", 3, 3)])
        self.assertEqual(self._accounts(resources["subgroups"][0]), ["Alfa", "Meio", "Zeta"])

    def test_two_actions_of_one_menu_are_two_subgroups(self):
        self._workflow("Alfa", 2)       # Enviar Recursos, also "Recursos e logistica"

        resources = self._page().context["workflow_groups"][1]

        self.assertEqual([(s["label"], s["shown"]) for s in resources["subgroups"]], [("Distribuir Recursos", 3), ("Enviar Recursos", 1)])
        self.assertEqual(resources["total"], 4)

    def test_group_by_account(self):
        response = self._page(group="account")

        groups = response.context["workflow_groups"]
        self.assertEqual([g["label"] for g in groups], ["Alfa", "Meio", "Zeta"])
        self.assertEqual(
            [s["label"] for s in groups[0]["subgroups"]],
            ["Construcao", "Recursos e logistica", "Alertas e monitoramento", "Conta"],
        )
        self.assertEqual((groups[0]["shown"], groups[0]["total"]), (4, 4))
        self.assertIn(f"?game_account={self.gas['Alfa'].pk}", groups[0]["filter_url"])

    def test_without_grouping_there_are_no_headers(self):
        response = self._page(group="none")

        groups = response.context["workflow_groups"]
        self.assertEqual((len(groups), groups[0]["label"], groups[0]["shown"]), (1, "", 12))
        self.assertNotIn("wf-group-check", response.content.decode())

    def test_the_choice_is_remembered(self):
        self._page(group="account", per_page=200)

        response = self._page()

        self.assertEqual(response.context["group_mode"], "account")
        self.assertEqual([o["label"] for o in response.context["per_page_options"] if o["active"]], ["200"])
        self.assertEqual(response.context["paginator"].per_page, 200)

    def test_page_sizes(self):
        self.assertEqual(self._page().context["paginator"].per_page, 100)                 # default
        self.assertEqual(self._page(per_page="all").context["paginator"].per_page, 1000)
        self.assertEqual([o["label"] for o in self._page().context["per_page_options"]], ["50", "100", "200", "Todos"])
        self.assertEqual(self._page(per_page=99999).context["paginator"].per_page, 1000)
        self.assertEqual(self._page(per_page="abc").context["paginator"].per_page, 100)

    def test_option_links_keep_the_filters(self):
        response = self._page(category="resources", page=1)

        for option in response.context["group_options"] + response.context["per_page_options"]:
            self.assertIn("category=resources", option["url"])
            self.assertNotIn("page=", option["url"].replace("per_page=", ""))
        self.assertEqual([g["key"] for g in response.context["workflow_groups"]], ["resources"])

    def test_headers_select_and_collapse_and_recurring_actions_are_marked(self):
        html = self._page().content.decode()

        # one selection box and one collapse button per menu and per action
        self.assertEqual(html.count("wf-group-check"), 8)
        self.assertEqual(html.count("data-wf-collapse="), 8)
        self.assertEqual(html.count("data-wf-group="), 8)
        self.assertIn("toggleCollapsed(key)", html)
        # distribute, login and the wine alert repeat by themselves; a construction plan is marked too
        self.assertEqual(html.count("> recorrente</span>"), 12)
        self.assertNotIn("badge badge-secondary hover:opacity-80", html)      # menu badge is redundant here
        # queue-wide lookups go through the component root: with $el (the clicked button)
        # "Selecionar pagina" and "Recolher tudo" found nothing
        self.assertIn("this.$root.querySelectorAll('.wf-bulk-check')", html)
        self.assertNotIn("this.$el.querySelector", html)

    def test_menu_badge_only_where_no_header_says_it(self):
        badge = "badge badge-secondary hover:opacity-80"
        self.assertNotIn(badge, self._page(group="account").content.decode())
        self.assertEqual(self._page(group="none").content.decode().count(badge), 12)

    def test_htmx_swap_carries_the_same_structure(self):
        partial = self.client.get(reverse("jobs:job-list"), HTTP_HX_REQUEST="true").content.decode()

        self.assertNotIn("<html", partial)
        self.assertIn("Agrupar por:", partial)
        self.assertEqual(partial.count("wf-bulk-check mt-1"), 12)


class CategoryTaxonomyTests(TestCase):
    def test_every_action_sits_in_a_known_menu(self):
        for code, meta in ACTION_CATALOG.items():
            self.assertIn(meta.get("category"), CATEGORY_META, code)
        self.assertEqual(sorted(CATEGORY_ORDER), sorted(CATEGORY_META))

    def test_the_catch_all_menus_are_gone(self):
        used = {meta["category"] for meta in ACTION_CATALOG.values()}
        self.assertFalse(used & {"economy", "automation", "admin"})
        # job forms switch behaviour on this key: it must not be renamed
        self.assertEqual(ACTION_CATALOG[PLAN]["category"], "construction")

    def test_where_the_actions_went(self):
        menus = {code: ACTION_CATALOG[code]["category"] for code in (2, 3, 23, 1006, 18, 27, 5, 1007, 11, 6, 25, 100, 1203, 30, 31, 701)}
        self.assertEqual(menus, {
            2: "resources", 3: "resources", 23: "resources", 1006: "resources",
            18: "research", 27: "research",
            5: "temple", 1007: "temple", 11: "temple",
            6: "account", 25: "account", 100: "account",
            1203: "military", 30: "diplomacy", 31: "diplomacy", 701: "monitoring",
        })

    def test_workflow_type_info(self):
        self.assertEqual(
            workflow_type_info("distribute"),
            {"action_code": 3, "label": "Distribuir Recursos", "category": "resources", "recurring": True},
        )
        self.assertEqual((workflow_type_info("diplomacy")["label"], workflow_type_info("diplomacy")["category"]), ("Diplomacia", "diplomacy"))
        self.assertEqual(workflow_type_info("transport_route")["label"], "Enviar Recursos")
        unknown = workflow_type_info("some_old_type")
        self.assertEqual((unknown["label"], unknown["category"], unknown["action_code"]), ("Some Old Type", "", None))

    def test_frozen_migration_map_matches_the_catalog(self):
        for meta in ACTION_CATALOG.values():
            runner = meta.get("runner")
            if runner:
                self.assertEqual(migration.TYPE_TO_CATEGORY[runner], workflow_type_info(runner)["category"], runner)

    def test_migration_rewrites_only_the_stored_label(self):
        node = Node.objects.create(name="node-mig")
        account = Account.objects.create(node=node, label="Mig", email="mig@example.com", password_enc="x")

        def stored(workflow_type, category):
            return Workflow.objects.create(account=account, node=node, workflow_type=workflow_type, category=category, status="waiting")

        rows = {
            "loop": stored("donate_loop", "automation"),
            "shrine": stored("activate_shrine_loop", "automation"),
            "login": stored("login_daily", "economy"),
            "diplo": stored("diplomacy", "monitoring"),
            "old": stored("retired_runner", "economy"),
            "pirate": stored("pirate", "military"),
            "odd": stored("retired_runner", "something"),
        }

        migration.forwards(django_apps, None)

        after = {key: Workflow.objects.get(pk=row.pk) for key, row in rows.items()}
        self.assertEqual(
            {key: row.category for key, row in after.items()},
            {"loop": "resources", "shrine": "temple", "login": "account", "diplo": "diplomacy",
             "old": "resources", "pirate": "military", "odd": "something"},
        )
        self.assertEqual({row.status for row in after.values()}, {"waiting"})
