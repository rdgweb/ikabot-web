"""N-140: troops and warships on their way count on the panel and in its history, until they land."""
from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from apps.game.models import AccountSnapshotHistory
from apps.game.services.kpi_history import _metrics
from apps.game.services.transit import units_in_transit


def _base(now, *moves):
    return {"gold": 1, "attack_alert_state": {"own_in_transit": [
        {"origin": "M4", "target": "M1", "troops": troops, "ships": ships, "arrives_at": int((now + delta).timestamp())}
        for troops, ships, delta in moves]}}


class TransitTests(SimpleTestCase):
    def test_only_what_has_not_landed_counts(self):
        now = timezone.now()
        base = _base(now, (50, 0, timedelta(minutes=10)), (0, 3, timedelta(minutes=30)), (40, 0, -timedelta(minutes=1)))
        transit = units_in_transit(base, at=now)
        self.assertEqual((transit["troops"], transit["ships"], len(transit["movements"])), (50, 3, 2))
        self.assertEqual(units_in_transit(base, at=now + timedelta(hours=1))["troops"], 0)
        self.assertEqual(units_in_transit({}, at=now), {"troops": 0, "ships": 0, "movements": []})

    def test_history_point_counts_what_was_travelling_then(self):
        now = timezone.now()
        row = AccountSnapshotHistory(captured_at=now, base_snapshot=_base(now, (347, 0, timedelta(minutes=20))), cities=[],
                                     military={"troops": {"Hoplita": 318}, "fleet": {}})
        self.assertEqual(_metrics(row)["troops"], 665)      # the ScorpiO case: 318 home + 347 on the road, not a drop
