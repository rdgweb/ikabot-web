"""N-187: the Maintenance page — config, backups (kept N, only with room on the disk), schedule, page access."""
import io
import os
import shutil
import tempfile
from collections import namedtuple
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import maintenance

Usage = namedtuple("Usage", "total used free")
PLENTY = Usage(100 * 1024 ** 3, 10 * 1024 ** 3, 90 * 1024 ** 3)


class FakeDump:
    """mariadb-dump that writes a small SQL file."""

    def __init__(self, command, stdout=None, stderr=None, env=None, code=0):
        assert "MYSQL_PWD" in env and not any("password" in str(part).lower() for part in command)
        self.stdout = io.BytesIO(b"CREATE TABLE x (id int);\n" * 100)
        self.stderr = io.BytesIO(b"")
        self.returncode = code

    def wait(self):
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@patch("apps.settings_app.maintenance._spawn", lambda work: work())
class MaintenanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.dir = tempfile.mkdtemp()
        self.patcher = patch.object(maintenance, "BACKUP_DIR", self.dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_config_is_kept_within_limits(self):
        config = maintenance.save_config({"backup_enabled": True, "backup_hour": 30, "backup_keep": 0, "optimize_weekday": 9})
        self.assertEqual((config["backup_enabled"], config["backup_hour"], config["backup_keep"], config["optimize_weekday"]),
                         (True, 23, 1, 6))
        self.assertEqual(maintenance.get_config(), config)

    def test_only_our_backup_files_are_served(self):
        open(os.path.join(self.dir, "ikabot_hub_20261120_0400.sql.gz"), "wb").close()
        self.assertTrue(maintenance.backup_path("ikabot_hub_20261120_0400.sql.gz"))
        for name in ("../etc/passwd", "ikabot_hub_20261120_0400.sql", "x.sql.gz", ""):
            self.assertIsNone(maintenance.backup_path(name))

    @patch("apps.settings_app.maintenance.shutil.disk_usage", lambda path: PLENTY)
    def test_a_backup_is_written_and_only_the_newest_are_kept(self):
        maintenance.save_config({"backup_keep": 2})
        for stamp in ("20261101_0400", "20261102_0400"):
            open(os.path.join(self.dir, f"ikabot_hub_{stamp}.sql.gz"), "wb").close()
        with patch("apps.settings_app.maintenance.subprocess.Popen", FakeDump):
            self.assertTrue(maintenance.start("backup"))
        names = [b["name"] for b in maintenance.backups()]
        self.assertEqual(len(names), 2)
        self.assertNotIn("ikabot_hub_20261101_0400.sql.gz", names)
        run = maintenance.last_run("backup")
        self.assertEqual(run["status"], "ok")
        self.assertFalse([n for n in os.listdir(self.dir) if n.endswith(".partial")])
        self.assertFalse(maintenance.busy())

    @patch("apps.settings_app.maintenance.shutil.disk_usage", lambda path: Usage(100, 99, 1))
    def test_no_backup_without_room_on_the_disk(self):
        with patch("apps.settings_app.maintenance.subprocess.Popen", FakeDump):
            maintenance.start("backup")
        self.assertEqual(maintenance.last_run("backup")["status"], "skipped")
        self.assertEqual(maintenance.backups(), [])

    @patch("apps.settings_app.maintenance.shutil.disk_usage", lambda path: PLENTY)
    def test_a_failed_dump_leaves_nothing_behind(self):
        with patch("apps.settings_app.maintenance.subprocess.Popen", lambda *a, **k: FakeDump(*a, **k, code=2)):
            maintenance.start("backup")
        self.assertEqual(maintenance.last_run("backup")["status"], "failed")
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_schedule_starts_the_backup_once_a_day(self):
        maintenance.save_config({"backup_enabled": True, "backup_hour": 0})
        with patch.object(maintenance, "_backup", lambda: ("feito", 1, "ok")):
            self.assertEqual(maintenance.tick(), "backup")
            cache.delete(maintenance.TICK_KEY)
            self.assertIsNone(maintenance.tick())                       # already ran today

    def test_the_optimisation_waits_for_the_retention(self):
        from .models import RetentionRun

        user = get_user_model().objects.create_user("opt-admin", is_staff=True)
        RetentionRun.objects.create(user=user, policy={}, status="running")
        maintenance.start("optimize")
        self.assertEqual(maintenance.last_run("optimize")["status"], "skipped")

    def test_an_interrupted_run_is_closed(self):
        maintenance._record({"id": "backup-1", "kind": "backup", "status": "running", "started_at": timezone.now().isoformat()})
        maintenance.tick()
        self.assertEqual(maintenance.last_run("backup")["status"], "failed")

    def test_the_page_is_for_administrators(self):
        url = reverse("settings_app:maintenance")
        user = get_user_model().objects.create_user("plain")
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(url).status_code, 200)
        user.is_staff = True
        user.save()
        response = self.client.get(url)
        self.assertContains(response, "Manutenção")
        self.assertContains(response, "Backup")
