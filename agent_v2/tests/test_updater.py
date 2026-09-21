import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import updater


class UpdaterSafetyTests(unittest.TestCase):
    def test_rejects_container_from_another_node(self):
        container = SimpleNamespace(
            name="ikabot-agent",
            id="agent-id",
            attrs={"Config": {"Env": ["AGENT_NODE_ID=other-node"]}},
        )
        with patch.object(updater.settings, "agent_node_id", "allowed-node"):
            with self.assertRaisesRegex(RuntimeError, "pertence ao no other-node"):
                updater._validate_target(container, "ikabot-agent")

    def test_replacement_uses_new_image_identity_and_embedded_version(self):
        config = {
            "Env": [
                "AGENT_NODE_ID=allowed-node",
                "AGENT_IMAGE=old/image:1.0.0",
                "AGENT_VERSION=1.0.0",
            ]
        }

        result = updater._replacement_environment(config, "new/image:1.1.0")

        self.assertIn("AGENT_IMAGE=new/image:1.1.0", result)
        self.assertFalse(any(item.startswith("AGENT_VERSION=") for item in result))

    def test_rejects_supervisor_as_target(self):
        container = SimpleNamespace(
            name="updater",
            id="abc123-full",
            attrs={"Config": {"Env": ["AGENT_NODE_ID=allowed-node"]}},
        )
        with patch.dict(os.environ, {"HOSTNAME": "abc123"}):
            with patch.object(updater.settings, "agent_node_id", "allowed-node"):
                with self.assertRaisesRegex(RuntimeError, "nao pode atualizar a si mesmo"):
                    updater._validate_target(container, "updater")


if __name__ == "__main__":
    unittest.main()
