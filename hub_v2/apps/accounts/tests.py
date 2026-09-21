from django.test import SimpleTestCase

from .services.agent_versions import classify_agent_version, parse_version


class AgentVersionTests(SimpleTestCase):
    def test_parse_version_accepts_release_and_optional_v_prefix(self):
        self.assertEqual(parse_version("0.1.55"), (0, 1, 55))
        self.assertEqual(parse_version("v2.10.3"), (2, 10, 3))

    def test_parse_version_rejects_partial_or_non_numeric_versions(self):
        self.assertIsNone(parse_version("0.1"))
        self.assertIsNone(parse_version("latest"))
        self.assertIsNone(parse_version("01.2.3"))

    def test_classify_agent_version(self):
        self.assertEqual(classify_agent_version("0.1.54", "0.1.55").code, "outdated")
        self.assertEqual(classify_agent_version("0.1.55", "0.1.55").code, "current")
        self.assertEqual(classify_agent_version("0.1.56", "0.1.55").code, "ahead")
        self.assertEqual(classify_agent_version("dev", "0.1.55").code, "invalid")
        self.assertEqual(classify_agent_version("", "0.1.55").code, "missing")
        self.assertEqual(classify_agent_version("0.1.55", "").code, "unverified")
