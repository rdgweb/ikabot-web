import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("version_guard.py")
SPEC = importlib.util.spec_from_file_location("version_guard", MODULE_PATH)
version_guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = version_guard
SPEC.loader.exec_module(version_guard)


class VersionGuardTests(unittest.TestCase):
    def test_strict_version(self):
        self.assertEqual(version_guard.parse_version("1.2.30", source="test"), (1, 2, 30))

    def test_rejects_non_release_values(self):
        for value in ("v1.2.3", "1.2", "01.2.3", "latest"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    version_guard.parse_version(value, source="test")

    def test_changed_component_requires_version_file(self):
        with patch.object(version_guard, "git", return_value="hub_v2/apps/example.py"):
            with self.assertRaisesRegex(ValueError, "hub_v2/VERSION must change"):
                version_guard.validate("base")

    def test_changed_component_requires_version_increase(self):
        changed = "hub_v2/apps/example.py\nhub_v2/VERSION"
        with patch.object(version_guard, "git", return_value=changed):
            with patch.object(version_guard, "version_at", return_value="0.2.75"):
                with self.assertRaisesRegex(ValueError, "version must increase"):
                    version_guard.validate("base")

    def test_changed_component_accepts_increased_version(self):
        changed = "hub_v2/apps/example.py\nhub_v2/VERSION"
        with patch.object(version_guard, "git", return_value=changed):
            with patch.object(version_guard, "version_at", return_value="0.2.74"):
                versions = version_guard.validate("base")
        self.assertEqual(versions["hub"], "0.2.75")


if __name__ == "__main__":
    unittest.main()
