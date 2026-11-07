"""A raid takes the merchant ships the loot needs, not every free one."""
import unittest

from runners.plunder import RaidCityRunner


class _Hub:
    def __init__(self, total):
        self.total = total
        self.logs = []

    def get_latest_spy_intel(self, **kwargs):
        return {"resources": {"wood": self.total}} if self.total is not None else None

    def report_log(self, job_id, level, msg):
        self.logs.append(msg)


def _size(total, free, looted=0, units=None):
    runner = RaidCityRunner.__new__(RaidCityRunner)
    runner.hub = _Hub(total)
    return runner._size_transporters("j", "ga", "1", units or {303: 30}, free, looted)


class RaidMerchantsTests(unittest.TestCase):
    def test_the_loot_decides(self):
        self.assertEqual(_size(50_000, 175), 100)          # 50.000 / 500
        self.assertEqual(_size(50_000, 175, looted=40_000), 20)

    def test_never_more_than_the_free_ones_and_all_without_a_reading(self):
        self.assertEqual(_size(439_314, 80), 80)
        self.assertEqual(_size(None, 175), 175)


if __name__ == "__main__":
    unittest.main()
