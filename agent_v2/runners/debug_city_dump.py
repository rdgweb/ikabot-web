"""Debug runner: dump raw city data to detect occupation fields."""
import json
import time

from runners.base import BaseRunner, RunnerResult
from core.runner_registry import register_runner


@register_runner(9001)
class DebugCityDumpRunner(BaseRunner):
    """Temporary debug runner — dumps raw game city data to logs."""

    def execute(self, job: dict) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]

        self.log(jid, "info", "DebugCityDump starting")
        client = self.get_game_session(aid)

        try:
            # Get global data (city list)
            global_data = client.get_global_data()
            cities = global_data.get("cities", [])
            self.log(jid, "info", f"Cities in global_data: {len(cities)}")
            for city in cities:
                self.log(jid, "info", f"GlobalData city: {json.dumps(city, ensure_ascii=False)}")

        except Exception as exc:
            self.log(jid, "warn", f"get_global_data failed: {exc}")

        try:
            # Try individual city screens for each city
            from apps.game.models import AccountSnapshot
            snap = AccountSnapshot.objects.filter(
                game_account__id=job.get("game_account_id")
            ).first()
            if snap:
                cities_snap = snap.cities or {}
                if isinstance(cities_snap, dict):
                    city_list = list(cities_snap.values())
                else:
                    city_list = cities_snap

                for city in city_list:
                    city_id = str(city.get("id") or "")
                    city_name = city.get("name", "?")
                    if not city_id:
                        continue
                    try:
                        self.log(jid, "info", f"Fetching raw data for {city_name} (id={city_id})")
                        raw = client._session.get(
                            client._server_url,
                            params={
                                "view": "city",
                                "cityId": city_id,
                                "backgroundView": "island",
                                "currentCityId": city_id,
                                "templateView": "city",
                                "actionRequest": client._action_request,
                                "ajax": "1",
                            },
                            timeout=15,
                        )
                        data = raw.json()
                        # Log updateGlobalData entry which has city details
                        for entry in data:
                            if isinstance(entry, list) and entry and entry[0] == "updateGlobalData":
                                payload = entry[1] if len(entry) > 1 else {}
                                cities_in_update = payload.get("cities", [])
                                for c in cities_in_update:
                                    if str(c.get("id") or c.get("cityId") or "") == city_id:
                                        self.log(jid, "info", f"RAW city {city_name}: {json.dumps(c, ensure_ascii=False)}")
                        time.sleep(1)
                    except Exception as exc2:
                        self.log(jid, "warn", f"City {city_name}: {exc2}")
        except Exception as exc:
            self.log(jid, "error", f"Dump failed: {exc}")
            import traceback
            self.log(jid, "error", traceback.format_exc())

        self.save_game_session(aid, client)
        return RunnerResult(success=True, data={"status": "dump_complete"})
