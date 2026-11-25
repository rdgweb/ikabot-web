from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Node, Account, GameAccount
from apps.billing.models import BillingAccount
from apps.orgs.models import Organization, Membership
from core.context_processors import nav_context


class AccessPolicyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="principal")
        self.other = Organization.objects.create(name="Outra", slug="other-policy")
        BillingAccount.objects.update_or_create(organization=self.org, defaults={"status":BillingAccount.EXEMPT})
        BillingAccount.objects.create(organization=self.other, status=BillingAccount.EXEMPT)
        self.root = self.person("principal", "owner", is_staff=True, is_superuser=True)
        self.staff = self.person("staff", "operator", is_staff=True)
        self.owner = self.person("owner", "owner")
        self.operator = self.person("operator", "operator")
        self.viewer = self.person("viewer", "viewer")
        self.legacy = self.person("legacy", "viewer")
        self.legacy.groups.add(Group.objects.get(name="admin"))
        self.empty = self.person("empty", None)

    def person(self, name, role, **flags):
        user = get_user_model()(username=name, **flags)
        user._own_organization = True
        user.set_password("test-only")
        user.save()
        if role:
            Membership.objects.create(user=user, organization=self.org, role=role)
        return user

    def test_platform_and_principal_routes_match_the_role_matrix(self):
        for user in (self.root, self.staff, self.owner, self.operator, self.viewer, self.legacy, self.empty):
            self.client.force_login(user)
            for path in ("/api/docs/", "/settings/manutencao/", "/settings/uso/"):
                with self.subTest(user=user.username, path=path):
                    self.assertEqual(self.client.get(path).status_code, 200 if user.is_staff else 404)
            self.assertEqual(self.client.get("/users/permissoes/").status_code, 200 if user.is_superuser else 404)
            self.assertEqual(self.client.get("/users/").status_code, 200 if user.is_superuser else 404)

    def test_staff_cannot_reset_a_superuser_password_even_by_direct_post(self):
        before = self.root.password
        self.client.force_login(self.staff)
        response = self.client.post(reverse("users:edit", args=[self.root.pk]), {
            "username":self.root.username,"password":"injected","is_active":"on"})
        self.assertEqual(response.status_code, 404)
        self.root.refresh_from_db()
        self.assertEqual(self.root.password, before)
        self.assertEqual(self.client.get("/django-admin/auth/user/").status_code, 404)

    def test_delegation_is_root_only_and_principal_and_final_owner_are_protected(self):
        path = reverse("users:access")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(path,{"action":"platform","user_id":self.owner.pk,"role":"platform"}).status_code,404)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_staff)
        self.client.force_login(self.root)
        self.client.post(path,{"action":"platform","user_id":self.owner.pk,"role":"platform"})
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_staff)  # Unpreviewed legacy writes are rejected.
        self.client.post(path,{"action":"platform","user_id":self.root.pk,"role":"organization"})
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser and self.root.is_staff)
        alone = Organization.objects.create(name="Sem outro dono",slug="single-owner-policy")
        member = Membership.objects.create(user=self.owner,organization=alone,role="owner")
        self.client.post(path,{"action":"membership","membership_id":member.pk,"role":"viewer"})
        member.refresh_from_db()
        self.assertEqual(member.role,"owner")

    def test_current_organization_controls_menu_and_operator_routes_not_any_membership_or_groups(self):
        Membership.objects.create(user=self.legacy,organization=self.other,role="owner")
        self.client.force_login(self.legacy)
        response = self.client.get("/")
        urls = [item["url"] for section in response.context["NAV_SECTIONS"] for item in section["items"]]
        self.assertNotIn("/accounts/",urls)
        self.assertNotIn("/settings/",urls)
        self.assertEqual(self.client.post("/accounts/create/",{}).status_code,403)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/accounts/create/").status_code,200)

    def test_docs_and_schema_deny_anonymous_and_customers_and_have_no_external_viewer(self):
        for user in (None, self.owner, self.legacy):
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.get("/api/docs/").status_code,404)
            self.assertEqual(self.client.get("/api/schema/").status_code,404)
        self.client.force_login(self.staff)
        page = self.client.get("/api/docs/")
        self.assertContains(page,"Scalar.createApiReference")
        self.assertContains(page,"/static/vendor/scalar/1.83.0/standalone.js")
        self.assertNotContains(page,"cdn.jsdelivr.net")
        schema = self.client.get("/api/schema/")
        self.assertEqual(schema.status_code,200)

    def test_viewer_and_expired_owner_cannot_operate_but_can_regularize(self):
        account = BillingAccount.objects.get(organization=self.org)
        account.status = BillingAccount.TRIAL
        account.trial_ends_at = timezone.now()-timedelta(seconds=1)
        account.save()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post("/accounts/create/",{}).status_code,402)
        self.assertEqual(self.client.get("/game/control/start/00000000-0000-0000-0000-000000000001/").status_code,402)
        self.assertEqual(self.client.get("/organizacao/").status_code,200)
        self.client.force_login(self.root)
        self.assertEqual(self.client.get("/users/permissoes/").status_code,200)


class AgentPolicyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="principal")
        self.other = Organization.objects.create(name="Other",slug="agent-other")
        self.billing, _ = BillingAccount.objects.update_or_create(organization=self.org,defaults={"status":BillingAccount.EXEMPT})
        BillingAccount.objects.create(organization=self.other,status=BillingAccount.EXEMPT)
        self.node,self.ga,self.token = self.make_node("a",self.org)
        self.sibling,self.sibling_ga,self.sibling_token = self.make_node("b",self.org)
        self.foreign,self.foreign_ga,self.foreign_token = self.make_node("c",self.other)
        self.client.defaults.update(HTTP_X_AGENT_TOKEN=self.token,HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def make_node(self,name,org):
        node = Node.objects.create(name=name,organization=org,transport="https")
        token = node.issue_agent_token();node.save()
        account=Account.objects.create(label=name,organization=org,node=node,email=name+"@example.com",password_enc="x")
        ga=GameAccount.objects.create(account=account,lobby_account_id=1,server_id="s1-br",server_language="br",server_number=1,name=name)
        return node,ga,token

    def test_piracy_and_job_resources_are_bound_to_the_node_not_just_the_organization(self):
        from apps.jobs.models import Job
        with patch("apps.piracy.services.next_for",return_value={"route":None,"hold":False}) as service:
            self.assertEqual(self.client.get("/api/agent/piracy/next/",{"game_account_id":self.ga.pk}).status_code,200)
            service.reset_mock()
            for ga in (self.foreign_ga,self.sibling_ga):
                self.assertEqual(self.client.get("/api/agent/piracy/next/",{"game_account_id":ga.pk}).status_code,404)
            service.assert_not_called()
        job=Job.objects.create(account=self.foreign_ga.account,node=self.foreign,action_code=1)
        self.assertEqual(self.client.post(f"/api/agent/jobs/{job.pk}/status/",data={"status":"running"}).status_code,404)
        job.refresh_from_db();self.assertEqual(job.status,"queued")

    def test_conflicting_and_malformed_resource_ids_do_not_reach_services(self):
        self.assertEqual(self.client.post("/api/agent/heartbeat/",{"node_id":self.sibling.pk}).status_code,403)
        self.assertEqual(self.client.get("/api/agent/piracy/next/",{"game_account_id":"bad"}).status_code,404)

    @override_settings(AGENT_TOKEN="legacy-key",AGENT_SHARED_TOKEN_ENABLED=True)
    def test_shared_key_is_bound_to_a_known_platform_node(self):
        self.client.defaults["HTTP_X_AGENT_TOKEN"]="legacy-key"
        self.assertEqual(self.client.get("/api/agent/operation/").status_code,200)
        self.client.defaults["HTTP_X_AGENT_NODE_ID"]=str(self.foreign.pk)
        self.assertEqual(self.client.get("/api/agent/operation/").status_code,401)

    def test_expiration_blocks_all_operational_channels_and_renewal_restores_access(self):
        self.billing.status=BillingAccount.TRIAL
        self.billing.trial_ends_at=timezone.now()-timedelta(seconds=1)
        self.billing.addons_free_until=timezone.now()+timedelta(days=10)
        self.billing.save()
        for path in ("/api/agent/config/","/api/agent/manual/next/","/api/agent/piracy/next/"):
            self.assertEqual(self.client.get(path,{"node_id":self.node.pk,"game_account_id":self.ga.pk}).status_code,403)
        self.assertEqual(self.client.post("/api/agent/jobs/claim/",{"node_id":self.node.pk,"slots":1}).status_code,403)
        state=self.client.get("/api/agent/operation/").json()
        self.assertFalse(state["operation_allowed"])
        with patch("apps.accounts.api.agent.recover_stale_running_jobs") as recovery:
            heartbeat=self.client.post("/api/agent/heartbeat/",{"node_id":self.node.pk})
            self.assertEqual(heartbeat.status_code,200)
            self.assertFalse(heartbeat.json()["operation_allowed"])
            recovery.assert_not_called()
        self.billing.trial_ends_at=timezone.now()+timedelta(days=1)
        self.billing.save()
        self.assertTrue(self.client.get("/api/agent/operation/").json()["operation_allowed"])

    def test_inactive_node_reports_diagnostics_but_never_operates(self):
        self.node.active=False;self.node.save()
        self.assertFalse(self.client.get("/api/agent/operation/").json()["operation_allowed"])
        self.assertEqual(self.client.get("/api/agent/config/",{"node_id":self.node.pk}).status_code,403)

    def test_every_primary_resource_policy_builds_a_valid_tenant_filtered_query(self):
        from uuid import uuid4
        from types import SimpleNamespace
        from rest_framework.exceptions import NotFound,PermissionDenied
        from core.auth.agent_policy import authorize_resources
        for key in ("account_id","game_account_id","ga_id","job_id","route_id","dump_id","config_id","cycle_id","task_id","offer_id","order_id","pk"):
            request=SimpleNamespace(data={},query_params={},path="/api/agent/captcha/challenge/" if key=="pk" else "/api/agent/market/orders/complete/")
            view=SimpleNamespace(kwargs={key:777 if key=="pk" else uuid4()})
            with self.subTest(key=key),self.assertRaises(NotFound):
                authorize_resources(request,view,self.node)
        with self.assertRaises(PermissionDenied):
            authorize_resources(request,SimpleNamespace(kwargs={"new_resource_id":uuid4()}),self.node)

    def test_trial_deadline_missing_period_grace_and_cancelled_period_boundaries(self):
        from apps.billing.services import state_of
        now=timezone.now()
        self.billing.status=BillingAccount.TRIAL
        self.billing.trial_ends_at=now
        self.assertFalse(state_of(self.billing,now).allows_work)
        self.billing.trial_ends_at=None
        self.assertFalse(state_of(self.billing,now).allows_work)
        self.billing.status=BillingAccount.ACTIVE
        self.billing.paid_until=now-timedelta(days=3,seconds=1)
        self.assertFalse(state_of(self.billing,now).allows_work)
        self.billing.paid_until=now-timedelta(days=2)
        self.assertTrue(state_of(self.billing,now).allows_work)
        self.billing.status=BillingAccount.CANCELED
        self.assertFalse(state_of(self.billing,now).allows_work)

    def test_expired_legacy_broker_dispatch_stays_queued_and_renewal_can_dispatch(self):
        from apps.jobs.models import Job
        from apps.jobs.services.dispatch import dispatch_job
        self.node.transport="redis";self.node.save()
        with patch("apps.jobs.signals.dispatch_job"):
            job=Job.objects.create(account=self.ga.account,game_account=self.ga,node=self.node,action_code=100)
        self.billing.status=BillingAccount.TRIAL
        self.billing.trial_ends_at=timezone.now()-timedelta(seconds=1)
        self.billing.save()
        with patch("apps.jobs.services.dispatch.celery_app.send_task") as send:
            dispatch_job(job)
            send.assert_not_called()
            job.refresh_from_db();self.assertEqual(job.status,"queued")
            self.billing.trial_ends_at=timezone.now()+timedelta(days=1);self.billing.save()
            dispatch_job(job)
            send.assert_called_once()

    def test_cross_node_campaign_delegation_requires_owned_coordinator_and_same_tenant(self):
        from apps.jobs.models import Job
        parent=Job.objects.create(account=self.ga.account,game_account=self.ga,node=self.node,action_code=16,status="running")
        url=f"/api/agent/jobs/{parent.pk}/spawn/"
        allowed=self.client.post(url,{"action_code":15,"inputs":{},"game_account_id":str(self.sibling_ga.pk)},content_type="application/json")
        self.assertEqual(allowed.status_code,201)
        child=Job._base_manager.get(pk=allowed.json()["new_job_id"])
        self.assertEqual(child.node_id,self.sibling.pk)
        self.assertEqual(self.client.post(url,{"action_code":15,"inputs":{},"game_account_id":str(self.foreign_ga.pk)},content_type="application/json").status_code,404)
        self.assertEqual(self.client.post(url,{"action_code":1,"inputs":{},"game_account_id":str(self.sibling_ga.pk)},content_type="application/json").status_code,404)

    def test_cross_node_parent_notice_requires_real_owned_child_relationship(self):
        from apps.jobs.models import Job
        parent=Job.objects.create(account=self.sibling_ga.account,node=self.sibling,action_code=16)
        child=Job.objects.create(account=self.ga.account,node=self.node,action_code=15,root_job_id=parent.pk)
        url=f"/api/agent/jobs/{parent.pk}/notify/"
        response=self.client.post(url,{"child_done":{"child_job_id":str(child.pk)}},content_type="application/json")
        self.assertEqual(response.status_code,409)  # authorized; the fixture has no campaign runner
        stranger=Job.objects.create(account=self.ga.account,node=self.node,action_code=15)
        self.assertEqual(self.client.post(url,{"child_done":{"child_job_id":str(stranger.pk)}},content_type="application/json").status_code,404)

    def test_captcha_solution_is_visible_only_to_its_node_including_legacy_owned_challenges(self):
        from apps.captcha.models import CaptchaChallenge
        for challenge in (CaptchaChallenge.objects.create(node=self.node,status="solved",solution="ABCD"),
                          CaptchaChallenge.objects.create(game_account=self.ga,status="solved",solution="ABCD")):
            self.assertEqual(self.client.get(f"/api/agent/captcha/challenge/{challenge.pk}/").status_code,200)
        foreign=CaptchaChallenge.objects.create(node=self.foreign,game_account=self.foreign_ga,status="solved",solution="SECRET")
        response=self.client.get(f"/api/agent/captcha/challenge/{foreign.pk}/")
        self.assertEqual(response.status_code,404)
        self.assertNotIn("SECRET",response.content.decode())
