from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ChangeLogEntry, Note, NoteEvent


class NotesViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="admin", password="x")
        self.client.force_login(self.user)

    def test_note_list_loads(self):
        Note.objects.create(title="Corrigir compra parcial", created_by=self.user)

        response = self.client.get(reverse("notes:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corrigir compra parcial")

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

    def test_create_changelog_entry_linked_to_note(self):
        note = Note.objects.create(title="Bug no mercado", created_by=self.user)

        response = self.client.post(
            reverse("notes:changelog-create"),
            {
                "visibility": "dev",
                "component": "hub",
                "version": "0.0.96",
                "dev_version": "0.0.96-dev",
                "published_version": "",
                "title": "Adiciona notas",
                "note": str(note.pk),
                "body": "Novo acompanhamento interno.",
            },
        )

        self.assertEqual(response.status_code, 302)
        entry = ChangeLogEntry.objects.get(title="Adiciona notas")
        self.assertEqual(entry.note, note)
        self.assertEqual(entry.created_by, self.user)
        self.assertEqual(entry.visibility, "dev")
