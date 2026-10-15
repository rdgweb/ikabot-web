import unittest
from types import SimpleNamespace
from unittest import mock
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



class PlatformAgentTests(unittest.TestCase):
    DIGEST = "blackoneal/ikabot-web-agent@sha256:" + "a" * 64

    def _job(self, **change):
        job = {"id": "p1", "action": "create", "node_id": "n1", "container_name": "ikahub-plat-abcdef12", "image": self.DIGEST,
               "environment": {"HUB_URL": "https://hub.example", "AGENT_NODE_ID": "n1", "AGENT_TOKEN": "t",
                               "AGENT_TRANSPORT": "https", "AGENT_NAME": "x", "AGENT_REQUIRE_PROXY": "1"}}
        job.update(change)
        return job

    def test_only_safe_requests_are_accepted(self):
        supervisor.validate_platform_request(self._job())
        for bad in ({"image": "evil/agent:latest"}, {"container_name": "ikabot-web-hub-1"},
                    {"environment": {**self._job()["environment"], "LD_PRELOAD": "x"}},
                    {"environment": {**self._job()["environment"], "HUB_URL": "http://hub:8000"}},
                    {"environment": {**self._job()["environment"], "AGENT_REQUIRE_PROXY": "0"}}):
            with self.assertRaises(RuntimeError):
                supervisor.validate_platform_request(self._job(**bad))

    def test_created_outside_the_internal_network_with_limits(self):
        client = mock.MagicMock()
        client.containers.list.return_value = []
        client.api.create_container.return_value = {"Id": "c1"}
        container = client.containers.get.return_value
        container.status = "running"
        container.name = "ikahub-plat-abcdef12"
        with mock.patch.object(supervisor, "HOST_ID", "h1"), mock.patch.object(supervisor.time, "sleep"):
            self.assertEqual(supervisor.create_platform_agent(client, self._job()), "ikahub-plat-abcdef12")
        kwargs = client.api.create_host_config.call_args.kwargs
        self.assertEqual(kwargs["network_mode"], "bridge")
        self.assertEqual(kwargs["cap_drop"], ["ALL"])
        self.assertLessEqual(kwargs["mem_limit"], supervisor.MAX_MEMORY)
        labels = client.api.create_container.call_args.kwargs["labels"]
        self.assertEqual((labels[supervisor.PLATFORM_LABEL], labels["com.ikabot.node-id"], labels["com.ikabot.host-id"]), ("true", "n1", "h1"))

    def test_remove_touches_only_platform_agents_of_this_host(self):
        own = mock.MagicMock()
        own.attrs = {"Config": {"Labels": {supervisor.PLATFORM_LABEL: "true", "com.ikabot.host-id": "h1", "com.ikabot.node-id": "n1"}}}
        other_host = mock.MagicMock()
        other_host.attrs = {"Config": {"Labels": {supervisor.PLATFORM_LABEL: "true", "com.ikabot.host-id": "h2", "com.ikabot.node-id": "n1"}}}
        client = mock.MagicMock()
        client.containers.list.return_value = [own, other_host]
        with mock.patch.object(supervisor, "HOST_ID", "h1"):
            self.assertEqual(supervisor.remove_platform_agent(client, "n1"), 1)
        own.remove.assert_called_once_with(force=True)
        other_host.remove.assert_not_called()
