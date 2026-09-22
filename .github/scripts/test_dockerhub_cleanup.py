import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("dockerhub_cleanup.py")
SPEC = importlib.util.spec_from_file_location("dockerhub_cleanup", MODULE_PATH)
dockerhub_cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = dockerhub_cleanup
SPEC.loader.exec_module(dockerhub_cleanup)


class ClassifyTests(unittest.TestCase):
    def test_latest_is_always_kept(self):
        keep, delete = dockerhub_cleanup.classify({"latest"})
        self.assertEqual(keep, {"latest"})
        self.assertEqual(delete, set())

    def test_sha_tags_are_always_deleted(self):
        keep, delete = dockerhub_cleanup.classify({"latest", "sha-6740c57", "sha-c1a1395"})
        self.assertEqual(delete, {"sha-6740c57", "sha-c1a1395"})
        self.assertEqual(keep, {"latest"})

    def test_v_prefixed_version_tags_are_kept(self):
        keep, delete = dockerhub_cleanup.classify({"latest", "v0.2.87", "v0.2.86"})
        self.assertEqual(keep, {"latest", "v0.2.87", "v0.2.86"})
        self.assertEqual(delete, set())

    def test_bare_version_is_deleted_only_when_v_prefixed_twin_exists(self):
        keep, delete = dockerhub_cleanup.classify({"latest", "0.2.87", "v0.2.87", "0.2.86"})

        # 0.2.87 has a "v0.2.87" twin — it's a pure duplicate, safe to delete.
        self.assertIn("0.2.87", delete)
        self.assertIn("v0.2.87", keep)
        # 0.2.86 has no "v"-prefixed twin — left alone, something might rely on it.
        self.assertIn("0.2.86", keep)

    def test_unrecognised_tag_names_are_never_deleted(self):
        keep, delete = dockerhub_cleanup.classify({"latest", "staging", "pr-42", "nightly"})
        self.assertEqual(delete, set())
        self.assertEqual(keep, {"latest", "staging", "pr-42", "nightly"})

    def test_realistic_mixed_history_matches_expectations(self):
        tags = {
            "latest",
            "v0.2.87", "0.2.87", "sha-6740c57",
            "v0.2.86", "0.2.86", "sha-c1a1395",
            "v0.2.85", "0.2.85", "sha-91e1139",
        }
        keep, delete = dockerhub_cleanup.classify(tags)

        self.assertEqual(keep, {"latest", "v0.2.87", "v0.2.86", "v0.2.85"})
        self.assertEqual(
            delete,
            {"0.2.87", "sha-6740c57", "0.2.86", "sha-c1a1395", "0.2.85", "sha-91e1139"},
        )


if __name__ == "__main__":
    unittest.main()
