import unittest
from types import SimpleNamespace
from unittest.mock import patch

import supervisor


class SupervisorSafetyTests(unittest.TestCase):
    def test_only_accepts_pinned_agent_repository_digest(self):
        supervisor.validate_image("blackoneal/ikabot-web-agent@sha256:" + "a" * 64)
        for image in (
            "blackoneal/ikabot-web-agent:latest",
            "other/image@sha256:" + "a" * 64,
            "blackoneal/ikabot-web-agent@sha256:invalid",
        ):
            with self.subTest(image=image), self.assertRaises(RuntimeError):
                supervisor.validate_image(image)

    def test_plain_http_only_to_a_service_of_the_same_compose(self):
        self.assertTrue(supervisor.hub_url_allowed("https://ikabot.example.com"))
        self.assertTrue(supervisor.hub_url_allowed("http://hub:8000"))
        for url in ("http://ikabot.example.com", "http://192.168.0.10:8000", "http://localhost:8000", "ftp://hub", "", "hub:8000"):
            with self.subTest(url=url):
                self.assertFalse(supervisor.hub_url_allowed(url))

    def test_existing_host_label_must_match(self):
        own = SimpleNamespace(id="supervisor-id")
        target = SimpleNamespace(
            id="agent-id",
            attrs={"Config": {"Env": ["AGENT_NODE_ID=node-1"], "Labels": {"com.ikabot.host-id": "other-host"}}},
        )
        target.reload = lambda: None
        client = SimpleNamespace(containers=SimpleNamespace(list=lambda all: [own, target]))
        with patch.object(supervisor, "HOST_ID", "allowed-host"):
            with patch.dict(supervisor.os.environ, {"HOSTNAME": "supervisor"}):
                with self.assertRaisesRegex(RuntimeError, "found 0"):
                    supervisor.find_target(client, "node-1")

    def test_replacement_removes_stale_version_override(self):
        result = supervisor.replacement_environment(
            {"Env": ["AGENT_VERSION=0.1.0", "AGENT_IMAGE=old", "AGENT_NODE_ID=node-1"]},
            "blackoneal/ikabot-web-agent@sha256:" + "a" * 64,
        )
        self.assertFalse(any(value.startswith("AGENT_VERSION=") for value in result))
        self.assertIn("AGENT_IMAGE=blackoneal/ikabot-web-agent@sha256:" + "a" * 64, result)


if __name__ == "__main__":
    unittest.main()
