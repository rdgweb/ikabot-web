"""Debug runner: dump raw city data to detect occupation fields. Action code: 9001."""
import json
import time
import re

from runners.base import BaseRunner, RunnerResult
from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS


@register_runner(9001)
class DebugCityDumpRunner(BaseRunner):
    """Temporary debug runner — dumps raw game city data to logs for analysis."""

    def execute(self, job: dict) -> RunnerResult:
        jid = job["job_id"]
        game_account_id = job.get("game_account_id", "")

        self.log(jid, "info", "DebugCityDump starting")
        client = self.get_game_client(game_account_id)
        session = client.session
        url_base = client._server_url

        # Fetch homepage to get city list
        self.log(jid, "info", "Fetching homepage...")
        html = session.get(url_base, timeout=30).text

        # Extract city IDs from HTML
        city_ids = re.findall(r'currentCityId=(\d+)', html)
        city_ids = list(dict.fromkeys(city_ids))  # deduplicate
        self.log(jid, "info", f"City IDs found: {city_ids}")

        # Update action_request
        ar_match = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', html)
        ar = ar_match.group(1) if ar_match else client._action_request

        for city_id in city_ids[:10]:
            time.sleep(1)
            self.log(jid, "info", f"Fetching city {city_id}...")
            try:
                resp = session.get(
                    url_base,
                    params={
                        "view": "city",
                        "cityId": city_id,
                        "backgroundView": "island",
                        "currentCityId": city_id,
                        "templateView": "city",
                        "actionRequest": ar,
                        "ajax": "1",
                    },
                    headers=dict(GAME_AJAX_HEADERS),
                    timeout=20,
                )
                ar_m = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', resp.text)
                if ar_m:
                    ar = ar_m.group(1)

                # Extract updateBackgroundData
                bg_match = re.search(
                    r'"updateBackgroundData",\s?([\s\S]*?)\],\s*\["updateTemplateData"',
                    resp.text,
                )
                if bg_match:
                    city_json = json.loads(bg_match.group(1), strict=False)
                    city_name = city_json.get("name", city_id)
                    self.log(jid, "info", f"CITY {city_name} keys: {sorted(city_json.keys())}")
                    self.log(jid, "info", f"CITY {city_name} raw: {json.dumps(city_json, ensure_ascii=False)[:3000]}")
                else:
                    self.log(jid, "warn", f"City {city_id}: could not extract updateBackgroundData")
                    # Log first 500 chars of response for debugging
                    self.log(jid, "debug", f"Response snippet: {resp.text[:500]}")

            except Exception as exc:
                self.log(jid, "error", f"City {city_id} failed: {exc}")

        self.save_game_client(game_account_id, client)
        self.log(jid, "info", "DebugCityDump complete")
        return RunnerResult(success=True, data={"status": "dump_complete"})
