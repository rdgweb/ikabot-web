import io
import re
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image, PngImagePlugin
from apps.orgs.models import Membership
from apps.orgs.services import register, signup_token, confirm_signup
from .account_services import profile_for
from .models import EmailChallenge, UserProfile, UserSession

PASSWORD = "Conta-Segura-2026!"
EMAIL = {"EMAIL_READY": True, "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend", "SITE_URL": "https://hub.example"}


@override_settings(**EMAIL)
class PersonalAccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("personal-a", "a@example.com", PASSWORD)
        self.other = get_user_model().objects.create_user("personal-b", "b@example.com", PASSWORD)
        self.client.force_login(self.user)
        self.client.get("/conta/")

    def post(self, **data):
        return self.client.post("/conta/", data)

    def last_link(self):
        return re.search(r"https://hub\.example(/\S+)", mail.outbox[-1].body).group(1)

    def another_session(self, user=None):
        client = Client()
        client.force_login(get_user_model().objects.get(pk=(user or self.user).pk))
        client.get("/conta/", HTTP_USER_AGENT="Mozilla/5.0 Windows Chrome/130.0", HTTP_X_REAL_IP="203.0.113.5")
        key = client.cookies[settings.SESSION_COOKIE_NAME].value
        return client, UserSession.objects.get(session_key=key)

    def test_profile_is_personal_does_not_change_email_or_privileges(self):
        self.post(action="profile", first_name="Rodrigo", last_name="Teste", display_name="Minha identidade", email="injected@example.com", user_id=self.other.pk, is_superuser="on")
        self.user.refresh_from_db();self.other.refresh_from_db()
        self.assertEqual((self.user.first_name,self.user.email,self.user.is_superuser),("Rodrigo","a@example.com",False))
        self.assertEqual(self.other.first_name, "")
        self.assertEqual(profile_for(self.user).display_name,"Minha identidade")
        self.assertContains(self.client.get("/conta/"),"Minha identidade")

    def test_avatar_is_resized_stripped_and_private(self):
        source=io.BytesIO();metadata=PngImagePlugin.PngInfo();metadata.add_text("private","secret camera metadata")
        Image.new("RGB",(600,400),"red").save(source,"PNG",pnginfo=metadata)
        self.post(action="profile",avatar=SimpleUploadedFile("photo.png",source.getvalue(),content_type="image/png"))
        response=self.client.get("/conta/avatar/")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response["Content-Type"],"image/jpeg")
        self.assertEqual(response["Cache-Control"],"private, no-store")
        image=Image.open(io.BytesIO(response.content));self.assertEqual(image.size,(256,256))
        self.assertNotIn(b"secret camera",response.content)
        stranger=Client();stranger.force_login(self.other)
        self.assertEqual(stranger.get("/conta/avatar/").status_code,404)
        self.post(action="profile",remove_avatar="on")
        self.assertEqual(self.client.get("/conta/avatar/").status_code,404)

    def test_invalid_oversized_and_animated_avatars_are_rejected(self):
        for filename,body in [("evil.svg",b'<svg onload="alert(1)"/>'),("big.png",b'x'*(2*1024*1024+1))]:
            self.assertEqual(self.post(action="profile",avatar=SimpleUploadedFile(filename,body)).status_code,400)
        self.assertFalse(profile_for(self.user).avatar)

    def test_preferences_persist_and_apply_to_other_pages(self):
        self.post(action="preferences",compact_tables="on",reduced_motion="on")
        profile=profile_for(self.user)
        self.assertTrue(profile.compact_tables and profile.reduced_motion)
        response=self.client.get("/conta/")
        self.assertContains(response,'ik-pref-compact ik-pref-motion')
        self.assertFalse(profile_for(self.other).reduced_motion)

    def test_email_change_requires_password_and_only_applies_after_confirmation_post(self):
        self.assertEqual(self.post(action="email",email="new@example.com",current_password="wrong").status_code,400)
        self.post(action="email",email="NEW@example.com",current_password=PASSWORD)
        self.user.refresh_from_db();self.assertEqual(self.user.email,"a@example.com")
        self.assertEqual(mail.outbox[-1].to,["new@example.com"])
        path=self.last_link();token=path.rstrip('/').split('/')[-1]
        self.assertNotEqual(EmailChallenge.objects.get(user=self.user).token_digest,token)
        self.assertEqual(self.client.get(path).status_code,200)
        self.user.refresh_from_db();self.assertEqual(self.user.email,"a@example.com")
        self.assertEqual(self.client.post(path).status_code,302)
        self.user.refresh_from_db();self.assertEqual(self.user.email,"new@example.com")
        self.assertTrue(profile_for(self.user).email_is_verified())
        self.assertEqual(self.client.post(path).status_code,410)

    def test_confirmation_is_owner_only_and_expires(self):
        self.post(action="verify_email")
        path=self.last_link()
        stranger=Client();stranger.force_login(self.other)
        self.assertEqual(stranger.get(path).status_code,410)
        self.assertEqual(Client().get(path).status_code,302)
        EmailChallenge.objects.filter(user=self.user).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post(path).status_code,410)
        self.assertFalse(profile_for(self.user).email_is_verified())

    def test_resend_invalidates_old_link_and_is_throttled(self):
        self.post(action="verify_email");old=self.last_link()
        self.post(action="verify_email")
        self.assertEqual(len(mail.outbox),1)
        UserProfile.objects.filter(user=self.user).update(last_email_sent_at=timezone.now()-timedelta(minutes=2))
        self.post(action="verify_email");new=self.last_link()
        self.assertNotEqual(old,new)
        self.assertEqual(self.client.post(old).status_code,410)
        self.assertEqual(self.client.post(new).status_code,302)

    def test_cancel_password_change_and_delivery_failure_preserve_current_email(self):
        self.post(action="email",email="new@example.com",current_password=PASSWORD);path=self.last_link()
        self.post(action="cancel_email")
        self.assertEqual(self.client.post(path).status_code,410)
        UserProfile.objects.filter(user=self.user).update(last_email_sent_at=None)
        with patch("apps.users.account_services.send_mail",side_effect=RuntimeError("unavailable")):
            self.post(action="email",email="new@example.com",current_password=PASSWORD)
        self.user.refresh_from_db();self.assertEqual(self.user.email,"a@example.com")
        self.assertEqual(EmailChallenge.objects.get(user=self.user).delivery,"failed")
        UserProfile.objects.filter(user=self.user).update(last_email_sent_at=None)
        self.post(action="verify_email");path=self.last_link()
        self.user.set_password("Another-Password-2026!");self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(path).status_code,410)

    def test_duplicate_email_is_not_sent_or_claimed(self):
        self.post(action="email",email="B@EXAMPLE.COM",current_password=PASSWORD)
        self.assertFalse(EmailChallenge.objects.filter(user=self.user).exists())
        self.user.refresh_from_db();self.assertEqual(self.user.email,"a@example.com")

    def test_legacy_addresses_are_unverified_without_locking_out_existing_user(self):
        self.assertFalse(profile_for(self.user).email_is_verified())
        self.assertEqual(self.client.get("/conta/?tab=security").status_code,200)
        self.post(action="verify_email")
        self.client.post(self.last_link())
        self.assertTrue(profile_for(self.user).email_is_verified())

    def test_session_listing_does_not_expose_session_keys_and_is_user_scoped(self):
        other_client,session=self.another_session()
        foreign_client,foreign=self.another_session(self.other)
        page=self.client.get("/conta/?tab=security")
        self.assertContains(page,"Chrome · Windows")
        self.assertNotContains(page,session.session_key)
        self.assertNotContains(page,foreign.session_key)
        self.assertNotContains(page,str(foreign.pk))
        self.assertContains(page,"Esta sessão")

    def test_revoking_other_session_needs_password_and_blocks_its_next_request(self):
        other,session=self.another_session()
        self.post(action="revoke_session",session_id=session.pk,current_password="wrong")
        self.assertEqual(other.get("/conta/").status_code,200)
        self.post(action="revoke_session",session_id=session.pk,current_password=PASSWORD)
        self.assertEqual(other.get("/conta/").status_code,302)
        self.assertEqual(self.client.get("/conta/").status_code,200)

    def test_foreign_and_current_session_cannot_be_revoked(self):
        _,foreign=self.another_session(self.other)
        self.assertEqual(self.post(action="revoke_session",session_id=foreign.pk,current_password=PASSWORD).status_code,404)
        current=UserSession.objects.get(session_key=self.client.cookies[settings.SESSION_COOKIE_NAME].value)
        self.post(action="revoke_session",session_id=current.pk,current_password=PASSWORD)
        current.refresh_from_db();self.assertIsNone(current.revoked_at)

    def test_password_and_email_changes_keep_current_session_and_revoke_others(self):
        other,_=self.another_session()
        new_password="Outra-Senha-Segura-2026!"
        self.post(action="password",old_password=PASSWORD,new_password1=new_password,new_password2=new_password)
        self.assertEqual(other.get("/conta/").status_code,302)
        self.assertEqual(self.client.get("/conta/").status_code,200)
        other,_=self.another_session()
        self.post(action="email",email="new@example.com",current_password=new_password)
        self.client.post(self.last_link())
        self.assertEqual(other.get("/conta/").status_code,302)
        self.assertEqual(self.client.get("/conta/").status_code,200)

    def test_logout_is_reflected_in_session_history(self):
        other,session=self.another_session()
        other.logout();session.refresh_from_db()
        self.assertEqual(session.reason,"logout")
        self.assertIsNotNone(session.revoked_at)

    @override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cached_db")
    def test_revoked_session_cannot_be_resurrected_from_cache_or_saved_by_inflight_request(self):
        other,tracked=self.another_session()
        row=Session.objects.get(session_key=tracked.session_key)
        saved=row.get_decoded()
        self.post(action="revoke_session",session_id=tracked.pk,current_password=PASSWORD)
        Session.objects.update_or_create(session_key=row.session_key,defaults={"session_data":row.session_data,"expire_date":row.expire_date})
        Store=import_module(settings.SESSION_ENGINE).SessionStore
        cache.set(Store(session_key=row.session_key).cache_key,saved,300)
        self.assertEqual(other.get("/conta/").status_code,302)

    def test_confirmation_and_session_revocation_require_csrf(self):
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post("/conta/",{"action":"revoke_others","current_password":PASSWORD}).status_code,403)

    def test_public_resend_only_reactivates_pending_signup_never_disabled_member(self):
        pending=register(email="pending@example.com",username="pending",password=PASSWORD,organization_name="My alliance")
        old=signup_token(pending,"My alliance","pt")
        anonymous=Client()
        response=anonymous.post("/conta/cadastro/reenviar/",{"email":"pending@example.com"})
        self.assertEqual(response.status_code,200)
        path=self.last_link();self.assertIsNone(confirm_signup(old))
        self.assertEqual(anonymous.get(path).status_code,302)
        pending.refresh_from_db();self.assertTrue(pending.is_active)
        self.assertTrue(profile_for(pending).email_is_verified())
        self.assertEqual(Membership.objects.get(user=pending).organization.name,"My alliance")
        self.other.is_active=False;self.other.save(update_fields=["is_active"])
        count=len(mail.outbox)
        disabled=anonymous.post("/conta/cadastro/reenviar/",{"email":self.other.email})
        unknown=anonymous.post("/conta/cadastro/reenviar/",{"email":"missing@example.com"})
        self.assertEqual(disabled.content,unknown.content)
        self.assertEqual(len(mail.outbox),count)

    def test_existing_session_discovery_preserves_session_contents_and_expiry(self):
        _,tracked=self.another_session()
        key=tracked.session_key
        row=Session.objects.get(session_key=key)
        snapshot=(row.session_data,row.expire_date)
        tracked.delete()
        from django.apps import apps
        import_module("apps.users.migrations.0003_existing_sessions").identify_sessions(apps,None)
        discovered=UserSession.objects.get(session_key=key)
        self.assertTrue(discovered.discovered)
        self.assertIsNone(discovered.last_seen_at)
        row.refresh_from_db();self.assertEqual((row.session_data,row.expire_date),snapshot)

    def test_sensitive_actions_are_rate_limited(self):
        other,tracked=self.another_session()
        for _ in range(10):
            self.post(action="revoke_others",current_password="wrong")
        self.post(action="revoke_others",current_password=PASSWORD)
        tracked.refresh_from_db();self.assertIsNone(tracked.revoked_at)

    def test_account_uses_full_navigation_to_apply_personal_preferences(self):
        response=self.client.get("/conta/?tab=security",HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code,204)
        self.assertEqual(response["HX-Redirect"],"/conta/?tab=security")


from django.test import TransactionTestCase, skipUnlessDBFeature


class ConcurrentEmailIdentityTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    @override_settings(**EMAIL)
    def test_two_accounts_cannot_confirm_the_same_email(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        from django.core.exceptions import ValidationError
        from django.test import RequestFactory
        from .account_services import confirm_email, digest, request_email_confirmation
        cache.clear()
        users=[]
        for n in range(2):
            user=get_user_model().objects.create_user(f"concurrent-email-{n}",f"before-{n}@example.com",PASSWORD)
            request=RequestFactory().post("/conta/");request.user=user
            with patch("apps.users.account_services.send_mail",return_value=1) as sender:
                request_email_confirmation(request,"shared@example.com",PASSWORD)
                token=re.search(r"/conta/email/confirmar/([^/]+)/",sender.call_args.args[1]).group(1)
            users.append((user.pk,token))
        def worker(item):
            close_old_connections()
            try:
                request=RequestFactory().post("/conta/");request.user=get_user_model().objects.get(pk=item[0])
                confirm_email(request,item[1])
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(worker,users)),1)
        self.assertEqual(get_user_model().objects.filter(email="shared@example.com").count(),1)
