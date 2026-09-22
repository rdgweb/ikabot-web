"""N-37: the USER_AGENTS pool must stay complete, Chromium-only, and in lockstep
with ikabotapi_overrides/SupportedUserAgents.json (see that folder's README for why
a mismatch silently breaks blackbox-token consistency)."""

import json
import re
import unittest
from pathlib import Path

from game_client.constants import USER_AGENTS

ROOT = Path(__file__).resolve().parents[2]
OVERRIDE_FILE = ROOT / "ikabotapi_overrides" / "SupportedUserAgents.json"

# A syntactically-plausible desktop Chrome/Chromium UA: engine build fixed at
# 537.36 (real Chrome UAs never vary this), Safari suffix matching AppleWebKit,
# and no trailing dot / truncation.
_CHROME_UA_RE = re.compile(
    r"^Mozilla/5\.0 \([^)]+\) AppleWebKit/537\.36 \(KHTML, like Gecko\) Chrome/\d+\.\d+\.\d+\.\d+ Safari/537\.36$"
)


class UserAgentPoolTests(unittest.TestCase):
    def test_pool_is_not_empty(self):
        self.assertGreaterEqual(len(USER_AGENTS), 2)

    def test_every_entry_is_a_complete_well_formed_chrome_ua(self):
        for ua in USER_AGENTS:
            self.assertRegex(ua, _CHROME_UA_RE, f"malformed or non-Chrome UA: {ua!r}")

    def test_no_entry_is_truncated_mid_token(self):
        # The exact defects this note was filed over: a dangling trailing dot,
        # or a Safari build number missing its final digit.
        for ua in USER_AGENTS:
            self.assertFalse(ua.endswith("."), f"trailing dot (truncated): {ua!r}")
            self.assertNotIn("Safari/537.3 ", ua + " ")

    def test_only_windows_and_linux_are_represented(self):
        for ua in USER_AGENTS:
            self.assertTrue(
                "Windows NT 10.0" in ua or "X11; Linux" in ua,
                f"unexpected platform: {ua!r}",
            )

    def test_pool_has_no_duplicates(self):
        self.assertEqual(len(USER_AGENTS), len(set(USER_AGENTS)))

    @unittest.skipUnless(
        OVERRIDE_FILE.exists(),
        "ikabotapi_overrides/ lives at the repo root, next to agent_v2 — not inside it, "
        "so it isn't present when only agent_v2/ is mounted/copied into a container.",
    )
    def test_matches_the_ikabotapi_override_file_exactly(self):
        """This is the coupling that matters: ikabotapi's TokenGenerator only honours
        a requested user_agent when it is in its SupportedUserAgents.json — otherwise
        it silently generates the blackbox token with an unrelated random UA while
        still using a real Chromium engine. If this list and the override drift apart,
        every login's blackbox token stops matching the UA sent to the game server."""
        override = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(set(USER_AGENTS), set(override))


if __name__ == "__main__":
    unittest.main()
