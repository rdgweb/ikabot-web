from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import DockerHost, Node

from .models import Note, NoteEvent
from .services import RegistryRelease, _release_from_payload, record_change


class ReleaseStatusTests(SimpleTestCase):
    def test_registry_payload_uses_highest_semantic_version(self):
        release = _release_from_payload(
            "hub",
            "blackoneal/ikabot-web-hub",
            {
                "results": [
                    {"name": "latest", "last_updated": "2026-09-21T15:00:00Z"},
                    {"name": "sha-deadbee", "last_updated": "2026-09-21T15:00:00Z"},
                    {"name": "v0.2.74", "last_updated": "2026-09-20T15:00:00Z"},
                    {
                        "name": "0.2.75",
                        "last_updated": "2026-09-21T15:00:00Z",
                        "digest": "sha256:abc",
                    },
                ]
            },
        )

        self.assertEqual(release.version, "0.2.75")
        self.assertEqual(release.image, "blackoneal/ikabot-web-hub:0.2.75")
        self.assertEqual(release.digest, "sha256:abc")


class NotesViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="admin", password="x")
        self.client.force_login(self.user)

    def test_note_list_loads(self):
        Note.objects.create(title="Corrigir compra parcial", created_by=self.user)

        response = self.client.get(reverse("notes:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corrigir compra parcial")

    @patch("apps.notes.views.get_registry_release")
    def test_updates_page_tracks_installed_and_published_versions(self, registry_release):
        registry_release.side_effect = [
            RegistryRelease("hub", "blackoneal/ikabot-web-hub", "0.2.76"),
            RegistryRelease("agent", "blackoneal/ikabot-web-agent", "0.1.55"),
        ]
        host = DockerHost.objects.create(
            name="docker-test",
            last_seen_at=timezone.now(),
        )
        Node.objects.create(
            name="blackshadow-node",
            agent_version="0.1.54",
            agent_last_seen_at=timezone.now(),
            docker_host=host,
        )

        response = self.client.get(reverse("notes:changelog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atualizacoes do sistema")
        self.assertContains(response, "v0.2.76")
        self.assertContains(response, "blackshadow-node")
        self.assertContains(response, "atualizacao disponivel")
        self.assertContains(response, "Atualizar pelo Hub")
        self.assertContains(response, "Atualizar agente")
        self.assertContains(response, "fara rollback se ele nao iniciar")
        self.assertNotContains(response, "return confirm(")
        self.assertNotContains(response, "Registrar")

    def test_create_note(self):
        response = self.client.post(
            reverse("notes:create"),
            {
                "title": "Bug no mercado",
                "note_type": "bug",
                "priority": "high",
                "status": "authorized",
                "source_url": "",
                "tags": "mercado",
                "body": "Compra interna nao dividiu em viagens.",
            },
        )

        self.assertEqual(response.status_code, 302)
        note = Note.objects.get(title="Bug no mercado")
        self.assertEqual(note.created_by, self.user)
        self.assertEqual(note.status, "authorized")
        self.assertIsNotNone(note.sequence)
        self.assertTrue(NoteEvent.objects.filter(note=note, event_type="created").exists())

    def test_claim_authorized_note_records_history(self):
        note = Note.objects.create(title="Task autorizada", status="authorized", created_by=self.user)

        response = self.client.post(reverse("notes:claim", kwargs={"pk": note.pk}))

        self.assertEqual(response.status_code, 302)
        note.refresh_from_db()
        self.assertEqual(note.status, "doing")
        self.assertEqual(note.claimed_by_label, "admin")
        self.assertTrue(NoteEvent.objects.filter(note=note, event_type="claimed").exists())

    def test_done_action_requests_approval_instead_of_completing(self):
        note = Note.objects.create(title="Task em andamento", status="doing", created_by=self.user)

        response = self.client.post(reverse("notes:done", kwargs={"pk": note.pk}))

        self.assertEqual(response.status_code, 302)
        note.refresh_from_db()
        self.assertEqual(note.status, "pending_approval")
        self.assertIsNone(note.completed_at)
        self.assertTrue(NoteEvent.objects.filter(note=note, event_type="approval_requested").exists())

    def test_approve_pending_note_completes_it(self):
        note = Note.objects.create(title="Task para revisar", status="pending_approval", created_by=self.user)

        response = self.client.post(reverse("notes:approve", kwargs={"pk": note.pk}))

        self.assertEqual(response.status_code, 302)
        note.refresh_from_db()
        self.assertEqual(note.status, "done")
        self.assertIsNotNone(note.completed_at)
        self.assertTrue(NoteEvent.objects.filter(note=note, event_type="approved").exists())
        self.assertTrue(NoteEvent.objects.filter(note=note, event_type="completed").exists())

    def test_record_change_service_links_entry_to_note(self):
        note = Note.objects.create(title="Bug no mercado", created_by=self.user)

        entry = record_change(
            title="Adiciona notas",
            body="Novo acompanhamento interno.",
            component="hub",
            version="0.0.96",
            note=note,
            username=self.user.username,
        )

        self.assertEqual(entry.note, note)
        self.assertEqual(entry.created_by, self.user)
        self.assertEqual(entry.visibility, "dev")
        self.assertTrue(
            NoteEvent.objects.filter(note=note, event_type="changelog").exists()
        )
