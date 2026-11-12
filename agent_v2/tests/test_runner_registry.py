"""Every game action the hub offers has its runner loaded by the agent (N-179: Roubar Bárbaros never ran because its
module was not imported)."""
import unittest

import runners  # noqa: F401 - importing the package registers the runners
from core.runner_registry import get_runner


class RunnerRegistryTests(unittest.TestCase):
    def test_barbarians_runner_is_registered(self):
        self.assertEqual(get_runner(19).__name__, "BarbariansRunner")
