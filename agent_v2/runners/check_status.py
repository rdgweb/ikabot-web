"""
Runner: check_status — logs into Ikariam and fetches full account status.

Action code: 100

Complete flow:
1. Resolve credentials from hub config
2. Login via GameClient (lobby auth + server login — unified flow)
3. Fetch game data: cities, global data, military
4. Build and push snapshot to hub

Collects:
- City list with resources, buildings, and population
- Gold, income, research info (base snapshot)
- Free transporters, production rates
- Military units per city
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import random
import re
import time
from typing import Any

import requests

from core.runner_registry import register_runner
from game_client.exceptions import LoginError
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


def _safe_num_like(val, default=0):
    """Parse numeric values coming from Ikariam JSON/HTML.

    Handles plain ints/floats and localized strings such as `1.139`, `1,139`,
    `1.139,0` and escaped HTML fragments.
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(float(val))

    raw = str(val).strip()
    if not raw:
        return default

    # Keep only the first numeric token if extra characters are present.
    match = re.search(r'-?[\d.,]+', raw)
    if not match:
        return default
    token = match.group(0)

    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
            token = "".join(parts)
        else:
            token = token.replace(",", ".")
    elif "." in token:
        parts = token.split(".")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
            token = "".join(parts)

    try:
        return int(float(token))
    except (ValueError, TypeError):
        return default


def _write_wine_debug(city_id: int, city_name: str, html: str) -> None:
    """Persist a small debug artifact with likely wine-related HTML snippets."""
    debug_dir = Path("/tmp/ik-wine-debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    keywords = [
        "wineConsumptionPerHour",
        "wineSpendings",
        "consumeWine",
        "currentResources",
        "maxResources",
        "producedTradegood",
        "tradegoodProduction",
        "population",
        "citizens",
        "tavern",
        "wine",
    ]

    snippets: list[str] = []
    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), html, re.IGNORECASE):
            start = max(0, match.start() - 220)
            end = min(len(html), match.end() + 320)
            snippet = html[start:end].replace("\n", " ").replace("\r", " ")
            snippets.append(f"=== {keyword} ===\n{snippet}\n")
            if len(snippets) >= 20:
                break
        if len(snippets) >= 20:
            break

    payload = [
        f"city_id={city_id}",
        f"city_name={city_name}",
        "",
        *snippets,
    ]
    (debug_dir / f"city-{city_id}.txt").write_text("\n".join(payload), encoding="utf-8")


def _merge_existing_building_progress(city_data: dict[str, Any], existing_city: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve useful construction metadata from the previous snapshot.

    The city page occasionally keeps `constructionSite` without re-sending a
    reliable end timestamp. When the previous snapshot had a still-future
    `construction_end_at` for the same building/position/level, keep it.
    """
    if not isinstance(city_data, dict) or not isinstance(existing_city, dict):
        return city_data

    now_ts = int(time.time())
    existing_by_position = {
        int(item.get("position")): item
        for item in (existing_city.get("buildings") or [])
        if isinstance(item, dict) and str(item.get("position", "")).strip() != ""
    }

    merged_buildings: list[dict[str, Any]] = []
    for item in city_data.get("buildings") or []:
        if not isinstance(item, dict):
            merged_buildings.append(item)
            continue
        current = dict(item)
        previous = existing_by_position.get(int(current.get("position", -1)))
        if not isinstance(previous, dict):
            merged_buildings.append(current)
            continue

        prev_end_at = _safe_num_like(previous.get("construction_end_at"), 0)
        same_building = str(previous.get("building") or "") == str(current.get("building") or "")
        same_level = _safe_num_like(previous.get("level"), 0) == _safe_num_like(current.get("level"), 0)
        if current.get("is_upgrading") and prev_end_at > now_ts and same_building and same_level:
            current.setdefault("construction_end_at", prev_end_at)
        merged_buildings.append(current)

    city_data["buildings"] = merged_buildings
    return city_data


@register_runner(100)
class CheckStatusRunner(BaseRunner):
    """Full account status check — login + collect all game data.

    Inputs (from job):
        (none required — credentials fetched from hub config)

    Result data:
        snapshot pushed to hub via update_snapshot API
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        self.log(jid, "info", "Iniciando verificação de status da conta")

        try:
            # ── 1. Resolve credentials ──
            creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
            if not creds:
                self.log(jid, "error", "Credenciais não encontradas para a conta")
                return RunnerResult(success=False, data={"error": "missing_credentials"})

            email = creds["email"]
            server = creds["server"]

            self.log(jid, "info", f"Servidor: {server} | Email: {email[:3]}***")

            # ── 2. Login via GameClient (unified auth flow) ──
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            # Extract account metadata from login result
            info = client.account_info
            player_name = info.get("player_name", "?")
            server_lang = info.get("server_lang", "")
            server_number = info.get("server_number", "")
            self.log(jid, "info", f"Conta: {player_name} | s{server_number}-{server_lang}")

            # Persist lobby token + game session
            new_lobby_token = client.lobby_token
            if ga_id:
                self.save_game_client(ga_id, client)
                self.hub.report_session(ga_id, client.export_cookies(), new_lobby_token)

            # ── 3. Fetch game data (uses client.session for raw GETs) ──
            session = client.session  # StrictProxySession with game headers set
            url_base = client._server_url + "?"

            # 3a. Fetch city list from homepage
            self.log(jid, "info", "Buscando lista de cidades...")
            html = session.get(url_base, timeout=30).text

            if "index.php?logout" in html or '<a class="logout"' in html:
                self.log(jid, "error", "Sessão expirada imediatamente após login")
                return RunnerResult(success=False, data={"error": "session_expired"})

            city_ids = self._extract_city_ids(html)
            self.log(jid, "info", f"Encontradas {len(city_ids)} cidades")

            # 3b. Fetch global data
            self.log(jid, "info", "Buscando dados globais...")
            global_data = self._fetch_global_data(session, url_base)

            # 3c. Fetch each city's details
            cities_data = []
            for i, cid in enumerate(city_ids):
                self.log(jid, "info", f"Buscando cidade {i + 1}/{len(city_ids)} (id={cid['id']})...")
                time.sleep(random.uniform(0.8, 2.0))  # Human-like delay
                city_data = self._fetch_city_data(session, url_base, cid["id"])
                if city_data:
                    if not city_data.get("tradegood") and cid.get("tradegood"):
                        city_data["tradegood"] = cid["tradegood"]
                    cities_data.append(city_data)
                    self.log(
                        jid, "info",
                        f"  {city_data.get('name', '?')} — "
                        f"Madeira: {city_data.get('wood', 0):,} | "
                        f"Vinho: {city_data.get('wine', 0):,} | "
                        f"Mármore: {city_data.get('marble', 0):,} | "
                        f"Cristal: {city_data.get('crystal', 0):,} | "
                        f"Enxofre: {city_data.get('sulfur', 0):,}",
                    )

            # 3d. Fetch military data
            self.log(jid, "info", "Buscando dados militares...")
            military_data = self._fetch_military_data(session, url_base, html, city_ids)

            # ── 4. Build and push snapshot ──
            self.log(jid, "info", "Enviando snapshot ao hub...")
            total_city_wine_consumption = sum(
                int(city.get("wine_consumption") or 0)
                for city in cities_data
                if isinstance(city, dict)
            )
            total_city_wine_consumption_gross = sum(
                int(city.get("wine_consumption_gross") or 0)
                for city in cities_data
                if isinstance(city, dict)
            )
            total_city_wine_savings = sum(
                int(city.get("wine_savings") or 0)
                for city in cities_data
                if isinstance(city, dict)
            )
            existing_snapshot = None
            try:
                existing_snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            except Exception:
                existing_snapshot = None
            existing_base = existing_snapshot.get("base_snapshot") if isinstance(existing_snapshot, dict) else {}
            existing_cities = existing_snapshot.get("cities") if isinstance(existing_snapshot, dict) else []
            existing_city_map = {
                str(city.get("id") or "").strip(): city
                for city in (existing_cities if isinstance(existing_cities, list) else [])
                if isinstance(city, dict) and str(city.get("id") or "").strip()
            }
            cities_data = [
                _merge_existing_building_progress(
                    city,
                    existing_city_map.get(str(city.get("id") or "").strip()),
                )
                for city in cities_data
                if isinstance(city, dict)
            ]
            cities_data = self._merge_city_building_sync_data(cities_data, existing_cities)
            snapshot = {
                "base_snapshot": {
                    "player_name": player_name,
                    "server_id": f"s{server_number}-{server_lang}",
                    "gold": global_data.get("gold", 0),
                    "income": global_data.get("income", 0),
                    "upkeep": global_data.get("upkeep", 0),
                    "scientists_upkeep": global_data.get("scientistsUpkeep", 0),
                    "free_transporters": global_data.get("freeTransporters", 0),
                    "max_transporters": global_data.get("maxTransporters", 0),
                    "resource_production": global_data.get("resourceProduction", 0),
                    "tradegood_production": global_data.get("tradegoodProduction", 0),
                    "wine_spendings": total_city_wine_consumption,
                    "wine_spendings_gross": total_city_wine_consumption_gross,
                    "wine_savings": total_city_wine_savings,
                    "wine_spendings_header": global_data.get("wineSpendings", 0),
                    "city_count": len(cities_data),
                    "current_favor": existing_base.get("current_favor", 0),
                    "daily_login_state": existing_base.get("daily_login_state") or {},
                    "research_state": existing_base.get("research_state") or {},
                    "academy_cities": existing_base.get("academy_cities") or {},
                    "academy_updated_at": existing_base.get("academy_updated_at", ""),
                    "attack_alert_state": existing_base.get("attack_alert_state") or {},
                    "research_city_id": existing_base.get("research_city_id"),
                    "research_city_name": existing_base.get("research_city_name", ""),
                    "research_current_type": existing_base.get("research_current_type", ""),
                    "research_updated_at": existing_base.get("research_updated_at", ""),
                    "shrine_city_id": existing_base.get("shrine_city_id"),
                    "shrine_city_name": existing_base.get("shrine_city_name", ""),
                    "shrine_level": existing_base.get("shrine_level", 0),
                    "shrine_researched_gods": existing_base.get("shrine_researched_gods") or [],
                    "shrine_gods": existing_base.get("shrine_gods") or {},
                    "shrine_updated_at": existing_base.get("shrine_updated_at", ""),
                },
                "cities": cities_data,
                "military": military_data,
                "source_job_id": jid,
            }

            try:
                self.hub.update_snapshot(aid, snapshot, game_account_id=ga_id)
                self.log(jid, "info", "Snapshot enviado com sucesso")
            except Exception as e:
                self.log(jid, "warning", f"Falha ao enviar snapshot: {e}")

            if self._building_options_refresh_needed(cities_data):
                try:
                    self.hub.spawn_job(jid, action_code=1001, inputs={}, delay_seconds=5 * 60)
                    self.log(jid, "info", "Sync de edificios agendado para complementar build options")
                except Exception as exc:
                    self.log(jid, "warning", f"Nao foi possivel agendar sync de edificios: {exc}")

            self.log(
                jid, "info",
                f"Status concluído: {len(cities_data)} cidades, "
                f"ouro={global_data.get('gold', 0):,}",
            )
            return RunnerResult(success=True, data=snapshot)

        except LoginError as e:
            self.log(jid, "error", f"Falha no login: {e}")
            return RunnerResult(success=False, data={"error": "login_failed", "detail": str(e)})
        except Exception as exc:
            logger.exception("CheckStatusRunner failed for account %s", aid)
            self.log(jid, "error", f"Erro inesperado: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _merge_city_building_sync_data(self, fresh_cities: list[dict], existing_cities_raw: Any) -> list[dict]:
        existing_cities = existing_cities_raw if isinstance(existing_cities_raw, list) else []
        existing_map = {
            str(city.get("id")): city
            for city in existing_cities
            if isinstance(city, dict) and str(city.get("id") or "").strip()
        }
        merged: list[dict] = []
        for city in fresh_cities:
            city_id = str(city.get("id") or "").strip()
            existing = existing_map.get(city_id) or {}
            if existing.get("available_build_options"):
                city["available_build_options"] = existing.get("available_build_options") or []
            if existing.get("build_options_synced_at"):
                city["build_options_synced_at"] = existing.get("build_options_synced_at")
            if existing.get("shrine_state"):
                city["shrine_state"] = existing.get("shrine_state") or {}
            if existing.get("miracle_state"):
                city["miracle_state"] = existing.get("miracle_state") or {}
            if existing.get("academy_state"):
                city["academy_state"] = existing.get("academy_state") or {}
            if "current_favor" in existing:
                city["current_favor"] = existing.get("current_favor")
            if existing.get("daily_login_bonus_city"):
                city["daily_login_bonus_city"] = True
            merged.append(city)
        return merged

    def _building_options_refresh_needed(self, cities: list[dict]) -> bool:
        stale_seconds = self.get_building_options_stale_seconds()
        now = datetime.now(timezone.utc)
        for city in cities:
            options = city.get("available_build_options") or []
            synced_at_raw = city.get("build_options_synced_at")
            buildings = city.get("buildings") or []
            has_empty_slot = any(
                str(item.get("building") or "").strip() == "empty"
                for item in buildings
                if isinstance(item, dict)
            )
            if not has_empty_slot:
                continue
            if not synced_at_raw:
                return True
            try:
                synced_at = datetime.fromisoformat(str(synced_at_raw).replace("Z", "+00:00"))
                if synced_at.tzinfo is None:
                    synced_at = synced_at.replace(tzinfo=timezone.utc)
                if (now - synced_at.astimezone(timezone.utc)).total_seconds() > stale_seconds:
                    return True
            except Exception:
                return True
            if not options:
                return True
        return False

    # ══════════════════════════════════════════════════════════════════
    # GAME DATA EXTRACTION (matching ikabot parsing)
    # ══════════════════════════════════════════════════════════════════

    def _extract_city_ids(self, html: str) -> list[dict]:
        """Extract city list from homepage HTML.

        ikabot extracts from: relatedCityData: JSON.parse('...')
        """
        cities = []

        # Method 1: relatedCityData JSON (primary — what ikabot uses)
        # The JSON is escaped with \" inside a JS string literal
        match = re.search(r"relatedCityData:\s*JSON\.parse\('(.*?)'\)", html)
        if match:
            try:
                raw = match.group(1)
                # Unescape: \" → " and \\\\ → \\ then \' → '
                clean = raw.replace('\\"', '"').replace("\\\\'", "'").replace("\\\\", "\\")
                data = json.loads(clean)
                for key, city_info in data.items():
                    if not isinstance(city_info, dict):
                        continue
                    if city_info.get("relationship") == "ownCity":
                        # key is like "city_27010"
                        cid = city_info.get("id", 0)
                        if not cid and "_" in key:
                            try:
                                cid = int(key.split("_")[1])
                            except (ValueError, IndexError):
                                pass
                        cities.append({
                            "id": int(cid),
                            "name": city_info.get("name", ""),
                            "coords": city_info.get("coords", ""),
                            "tradegood": int(city_info.get("tradegood", 0)),
                        })
                if cities:
                    logger.info("Extracted %d cities from relatedCityData", len(cities))
                    return cities
            except Exception as e:
                logger.warning("Failed to parse relatedCityData: %s", e)

        # Method 2: dropdown/citySelect options
        pattern = re.compile(
            r'cityId[=:](\d+).*?(?:cityName|name)[=:]["\'](.*?)["\']',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            cities.append({"id": int(m.group(1)), "name": m.group(2), "coords": "", "tradegood": 0})

        # Method 3: at minimum, get current city
        if not cities:
            m = re.search(r'currentCityId["\s:=]+(\d+)', html)
            if m:
                cities.append({"id": int(m.group(1)), "name": "", "coords": "", "tradegood": 0})

        return cities

    def _fetch_global_data(self, session: requests.Session, url_base: str) -> dict:
        """Fetch global account data (gold, income, transporters, production).

        ikabot: GET view=updateGlobalData&ajax=1
        Response: JSON array, data at [0][1]["headerData"]
        """
        try:
            resp = session.get(
                f"{url_base}view=updateGlobalData&ajax=1",
                timeout=30,
            )
            data = json.loads(resp.text, strict=False)
            # ikabot: json_data = data[0][1]["headerData"]
            header_data = data[0][1]["headerData"]

            return {
                "gold": _safe_num_like(header_data.get("gold")),
                "income": _safe_num_like(header_data.get("income")),
                "upkeep": _safe_num_like(header_data.get("upkeep")),
                "scientistsUpkeep": _safe_num_like(header_data.get("scientistsUpkeep")),
                "freeTransporters": _safe_num_like(header_data.get("freeTransporters")),
                "maxTransporters": _safe_num_like(header_data.get("maxTransporters")),
                # NOTE: production rates from the Ikariam API are per-SECOND, not per-hour
                # Dashboard must convert: rate_per_hour = rate * 3600
                "resourceProduction": float(header_data.get("resourceProduction", 0) or 0),
                "tradegoodProduction": float(header_data.get("tradegoodProduction", 0) or 0),
                "wineSpendings": _safe_num_like(header_data.get("wineSpendings")),
                "currentResources": header_data.get("currentResources", {}),
            }
        except Exception as e:
            logger.warning("Failed to fetch global data: %s", e)
            return {}

    def _fetch_city_data(self, session: requests.Session, url_base: str, city_id: int) -> dict | None:
        """Fetch detailed city data.

        ikabot: GET view=city&cityId={city_id}
        JSON is between "updateBackgroundData" and "updateTemplateData" in HTML.
        """
        try:
            resp = session.get(f"{url_base}view=city&cityId={city_id}", timeout=30)
            html = resp.text

            # Extract JSON between updateBackgroundData and updateTemplateData
            # ikabot: re.search(r'"updateBackgroundData",\s?([\s\S]*?)\],\["updateTemplateData"', html)
            match = re.search(
                r'"updateBackgroundData",\s?([\s\S]*?)\],\s*\["updateTemplateData"',
                html,
            )
            if not match:
                logger.warning("Could not extract city JSON for city %d", city_id)
                return None

            city_json_str = match.group(1)
            city = json.loads(city_json_str, strict=False)

            def parse_js_json(var_name: str) -> dict[str, Any]:
                match = re.search(rf'{var_name}:\s*JSON\.parse\(\'(.*?)\'\)', html)
                if not match:
                    return {}
                raw = match.group(1)
                try:
                    clean = raw.replace('\\"', '"').replace("\\\\'", "'").replace("\\\\", "\\")
                    return json.loads(clean)
                except Exception:
                    return {}

            current_resources = parse_js_json("currentResources")
            max_resources = parse_js_json("maxResources")
            branch_office_resources = parse_js_json("branchOfficeResources")

            # Extract population data from currentResources JSON
            population = int(float(current_resources.get("population", 0) or 0)) if current_resources else 0
            citizens = int(float(current_resources.get("citizens", 0) or 0)) if current_resources else 0

            # Fallback to the legacy regex if the JS payload is missing.
            if current_resources:
                wood = int(float(current_resources.get("resource", 0) or 0))
                marble = int(float(current_resources.get("2", 0) or 0))
                wine = int(float(current_resources.get("1", 0) or 0))
                crystal = int(float(current_resources.get("3", 0) or 0))
                sulfur = int(float(current_resources.get("4", 0) or 0))
            else:
                # "resource":wood,"1":wine,"2":marble,"3":crystal,"4":sulfur
                res_match = re.search(
                    r'"resource":(\d+),"2":(\d+),"1":(\d+),"4":(\d+),"3":(\d+)',
                    html,
                )
                wood = wine = marble = crystal = sulfur = 0
                if res_match:
                    wood = int(res_match.group(1))
                    marble = int(res_match.group(2))
                    wine = int(res_match.group(3))
                    sulfur = int(res_match.group(4))    # key "4" = sulfur
                    crystal = int(res_match.group(5))   # key "3" = crystal

            # Extract warehouse capacity (ikabot pattern: maxResources: JSON.parse('{"resource":NNN,...)
            warehouse_capacity = int(float(max_resources.get("resource", 0) or 0)) if max_resources else 0
            if not warehouse_capacity:
                wh_match = re.search(r'maxResources:\s*JSON\.parse\(\'\{\\"resource\\":(\d+),', html)
                if not wh_match:
                    # Fallback: try inline JSON format
                    wh_match = re.search(r'"maxResources":(\d+)', html)
                warehouse_capacity = int(wh_match.group(1)) if wh_match else 0

            # Extract per-city wine consumption.
            # The city HTML exposes multiple wine values. The one that matches
            # the in-game city/resource panel is the JSON-ish block that comes
            # with producedTradegood/currentResources.
            wine_patterns = [
                r'"producedTradegood":\d+,"wineSpendings":([-\d.,]+)',
                r'"producedTradegood":"\d+".*?"wineSpendings":([-\d.,]+)',
                r'\\?"wineConsumptionPerHour\\?"\s*:\s*\\?"?([-\d.,]+)',
                r'wineConsumptionPerHour\s*:\s*"?(?:\\?")?([-\d.,]+)',
                r'consumeWine\s*:\s*"?(?:\\?")?([-\d.,]+)',
                r'/\*\s*wine spendings in city per hour\s*\*/\s*wineSpendings:\s*([-\d.,]+)',
            ]
            wine_consumption = 0
            for pattern in wine_patterns:
                wine_match = re.search(pattern, html)
                if wine_match:
                    wine_consumption = abs(_safe_num_like(wine_match.group(1), default=0))
                    if wine_consumption:
                        break
            wine_gross_match = re.search(
                r'/\*\s*wine spendings in city per hour\s*\*/\s*wineSpendings:\s*([-\d.,]+)',
                html,
            )
            wine_consumption_gross = (
                abs(_safe_num_like(wine_gross_match.group(1), default=0))
                if wine_gross_match else 0
            )
            if wine_consumption_gross == 0 and wine_consumption > 0:
                wine_consumption_gross = wine_consumption
            wine_savings = max(wine_consumption_gross - wine_consumption, 0)
            if wine_consumption == 0:
                _write_wine_debug(city_id, str(city.get("name") or ""), html)

            # Extract free citizens from HTML as fallback
            free_citizens_match = re.search(r'js_GlobalMenu_citizens">(.*?)</span>', html)
            free_citizens = 0
            if free_citizens_match:
                free_citizens_str = re.sub(r'\D', '', free_citizens_match.group(1))
                free_citizens = int(free_citizens_str) if free_citizens_str else 0

            produced_tradegood_match = re.search(r'producedTradegood:\s*"?(\d+)"?', html)
            produced_tradegood = int(produced_tradegood_match.group(1)) if produced_tradegood_match else 0

            resource_prod_match = re.search(r'resourceProduction:\s*([0-9.]+)', html)
            resource_production_per_hour = (
                round(float(resource_prod_match.group(1)) * 3600, 1)
                if resource_prod_match else 0.0
            )
            tradegood_prod_match = re.search(r'tradegoodProduction:\s*([0-9.]+)', html)
            tradegood_production_per_hour = (
                round(float(tradegood_prod_match.group(1)) * 3600, 1)
                if tradegood_prod_match else 0.0
            )

            # Parse buildings from position array
            slot_types_by_position: dict[int, str] = {}
            for pos_match in re.finditer(
                r'id=["\']position(\d+)["\'][^>]*class=["\'][^"\']*building\s+([^"\']+)["\']',
                html,
                re.IGNORECASE,
            ):
                raw_classes = pos_match.group(2)
                classes = [part.strip() for part in raw_classes.split() if part.strip()]
                slot_type = ""
                if "buildingGround" in classes:
                    idx = classes.index("buildingGround")
                    if idx + 1 < len(classes):
                        slot_type = classes[idx + 1]
                elif "constructionSite" in classes:
                    idx = classes.index("constructionSite")
                    if idx + 1 < len(classes):
                        slot_type = classes[idx + 1]
                if slot_type:
                    slot_types_by_position[int(pos_match.group(1))] = slot_type

            dom_state_by_position: dict[int, dict[str, Any]] = {}
            for pos_match in re.finditer(
                r'id=["\']position(\d+)["\'][^>]*class=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            ):
                classes = [part.strip() for part in pos_match.group(2).split() if part.strip()]
                dom_state_by_position[int(pos_match.group(1))] = {
                    "classes": classes,
                    "is_busy": "constructionSite" in classes,
                    "is_empty": "buildingGround" in classes,
                }

            buildings = []
            positions = city.get("position", [])
            for i, pos in enumerate(positions):
                if not isinstance(pos, dict):
                    continue
                building_name = pos.get("building", "")
                slot_type = str(pos.get("type") or slot_types_by_position.get(i) or "").strip()
                level = int(pos.get("level", 0)) if "level" in pos else 0
                json_is_busy = "constructionSite" in building_name
                dom_state = dom_state_by_position.get(i) or {}
                is_busy = bool(dom_state.get("is_busy")) if dom_state else json_is_busy
                if json_is_busy:
                    building_name = building_name.replace("constructionSite", "")
                if "buildingGround" in building_name or bool(dom_state.get("is_empty")):
                    building_name = "empty"

                # Extract construction end time when building is upgrading.
                # The game's position JSON may provide constructionEnd or enddate
                # as a Unix timestamp. Store it so runners can calculate accurate
                # wait times instead of relying solely on ika-tools estimates.
                construction_end_at = 0
                if is_busy:
                    raw_end = (
                        pos.get("constructionEnd")
                        or pos.get("enddate")
                        or pos.get("construction_end")
                        or 0
                    )
                    try:
                        construction_end_at = int(float(raw_end or 0))
                    except Exception:
                        construction_end_at = 0
                    # Ikariam puts the construction end time at city level (endUpgradeTime)
                    # for new builds; the per-position fields may be absent.
                    if not construction_end_at:
                        under = city.get("underConstruction")
                        if under is not None and int(float(under or -1)) == i:
                            try:
                                construction_end_at = int(float(city.get("endUpgradeTime") or 0))
                            except Exception:
                                construction_end_at = 0

                entry: dict = {
                    "position": i,
                    "building": building_name.strip(),
                    "type": slot_type,
                    "level": level,
                    "is_upgrading": is_busy,
                }
                if construction_end_at:
                    entry["construction_end_at"] = construction_end_at
                buildings.append(entry)

            def safe_int(val, default=0):
                if val is None:
                    return default
                try:
                    return int(float(str(val)))
                except (ValueError, TypeError):
                    return default

            return {
                "id": city.get("id", city_id),
                "name": city.get("name", ""),
                "island_id": safe_int(city.get("islandId")),
                "x": safe_int(city.get("islandXCoord")),
                "y": safe_int(city.get("islandYCoord")),
                "tradegood": produced_tradegood or safe_int(city.get("tradegood")),
                "produced_tradegood": produced_tradegood,
                "wood": wood,
                "wine": wine,
                "marble": marble,
                "crystal": crystal,
                "sulfur": sulfur,
                "warehouse_capacity": warehouse_capacity,
                "current_resources": current_resources,
                "market_resources": branch_office_resources,
                "max_resources": max_resources,
                "resource_production_per_hour": resource_production_per_hour,
                "tradegood_production_per_hour": tradegood_production_per_hour,
                "wine_consumption": wine_consumption,
                "wine_consumption_gross": wine_consumption_gross,
                "wine_savings": wine_savings,
                "population": population,
                "max_pop": population,
                "citizens": citizens,
                "free_citizens": free_citizens,
                "buildings": buildings,
                # Occupation fields (present when city/port is occupied by an enemy)
                "city_occupied": city.get("cityOccupied") or None,
                "harbour_occupied": city.get("harbourOccupied") or None,
                "occupier_id": city.get("occupierId") or None,
                "occupier_name": city.get("occupierName") or None,
                "port_controller_id": city.get("portControllerId") or None,
                "port_controller_name": city.get("portControllerName") or None,
            }

        except json.JSONDecodeError as e:
            logger.warning("JSON parse error for city %d: %s", city_id, e)
            return None
        except Exception as e:
            logger.warning("Failed to fetch city %d: %s", city_id, e)
            return None

    # ══════════════════════════════════════════════════════════════════
    # MILITARY DATA
    # ══════════════════════════════════════════════════════════════════

    def _fetch_military_data(
        self,
        session: requests.Session,
        url_base: str,
        homepage_html: str,
        city_ids: list[dict],
    ) -> dict:
        """Fetch military units from cityMilitary view for each city.

        Returns aggregated totals plus a per-city breakdown.
        """
        # Extract actionRequest token from homepage
        ar_match = re.search(r'actionRequest"?\s*:\s*"(.*?)"', homepage_html)
        if not ar_match:
            logger.warning("Could not find actionRequest token — skipping military")
            return {}

        action_request = ar_match.group(1)
        all_troops: dict[str, int] = {}
        all_fleet: dict[str, int] = {}
        by_city: list[dict[str, Any]] = []

        for cid_info in city_ids:
            cid = cid_info["id"]
            try:
                city_troops = self._fetch_military_tab(
                    session, url_base, cid, action_request, active_tab="tabUnits"
                )
                city_fleet = self._fetch_military_tab(
                    session, url_base, cid, action_request, active_tab="tabShips"
                )
                for name, count in city_troops.items():
                    all_troops[name] = all_troops.get(name, 0) + count
                for name, count in city_fleet.items():
                    all_fleet[name] = all_fleet.get(name, 0) + count
                by_city.append({
                    "city_id": cid,
                    "troops": city_troops,
                    "fleet": city_fleet,
                })

            except Exception as e:
                logger.warning("Military fetch failed for city %d: %s", cid, e)

        if all_troops or all_fleet:
            logger.info("Military data: %d troop types, %d fleet types", len(all_troops), len(all_fleet))

        return {"troops": all_troops, "fleet": all_fleet, "by_city": by_city}

    def _fetch_military_tab(
        self,
        session: requests.Session,
        url_base: str,
        city_id: int,
        action_request: str,
        active_tab: str,
    ) -> dict[str, int]:
        """Fetch and parse a single military tab for one city."""
        time.sleep(random.uniform(0.5, 1.2))
        resp = session.get(
            f"{url_base}view=cityMilitary&activeTab={active_tab}"
            f"&cityId={city_id}&backgroundView=city&currentCityId={city_id}"
            f"&currentTab={'multiTab2' if active_tab == 'tabShips' else 'multiTab1'}"
            f"&actionRequest={action_request}&ajax=1",
            timeout=30,
        )
        data = json.loads(resp.text, strict=False)
        full_html = data[1][1][1] if len(data) > 1 and len(data[1]) > 1 else ""
        if not full_html:
            return {}

        troops, fleet = self._parse_military_html(full_html)
        if active_tab == "tabShips":
            return fleet
        return troops

    @staticmethod
    def _parse_military_html(html: str) -> tuple[dict[str, int], dict[str, int]]:
        """Parse unit names and amounts from cityMilitary HTML."""
        troops: dict[str, int] = {}
        fleet: dict[str, int] = {}

        # Newer Ikariam markup renders unit icons in a header row and counts
        # in a separate row, so parse those columns positionally first.
        table_matches = re.findall(
            r'<table[^>]+class="[^"]*militaryList[^"]*"[^>]*>(.*?)</table>',
            html,
            re.DOTALL,
        )
        if table_matches:
            for table_html in table_matches:
                headers = re.findall(
                    r'<th[^>]*>\s*<div[^>]+class="(?:army|fleet)\s+(s\d+)"[^>]*>.*?'
                    r'<div[^>]+class="tooltip"[^>]*>(.*?)</div>',
                    table_html,
                    re.DOTALL,
                )
                count_row_match = re.search(
                    r'<tr[^>]+class="count"[^>]*>(.*?)</tr>',
                    table_html,
                    re.DOTALL,
                )
                if headers and count_row_match:
                    count_cells = re.findall(
                        r"<td[^>]*>(.*?)</td>",
                        count_row_match.group(1),
                        re.DOTALL,
                    )
                    if len(count_cells) >= len(headers) + 1:
                        for (css_class, unit_name_raw), td_content in zip(headers, count_cells[1:]):
                            unit_name = re.sub(r"<[^>]+>", "", unit_name_raw).strip()
                            raw = re.sub(r"<[^>]+>", "", td_content).strip().replace("\xa0", "")
                            if not unit_name or not raw or raw == "-":
                                continue
                            try:
                                amount = int(raw.replace(",", "").replace(".", ""))
                            except ValueError:
                                continue
                            if amount <= 0:
                                continue
                            try:
                                class_num = int(css_class[1:])
                            except (ValueError, IndexError):
                                continue
                            if 200 <= class_num < 300:
                                fleet[unit_name] = fleet.get(unit_name, 0) + amount
                            elif 300 <= class_num < 400:
                                troops[unit_name] = troops.get(unit_name, 0) + amount

            if troops or fleet:
                return troops, fleet

        # Fallback for older row-based markup.
        for row in re.split(r"<tr[^>]*>", html):
            army_match = re.search(
                r'<div[^>]+class="army\s+(s\d+)"[^>]*>.*?'
                r'<div[^>]+class="tooltip"[^>]*>(.*?)</div>',
                row, re.DOTALL,
            )
            if not army_match:
                continue

            css_class = army_match.group(1).strip()
            unit_name = re.sub(r"<[^>]+>", "", army_match.group(2)).strip()

            if not unit_name:
                continue

            # Find amount: first <td> with a numeric value in this row
            amount = 0
            for td_content in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL):
                raw = re.sub(r"<[^>]+>", "", td_content).strip().replace("\xa0", "")
                if not raw or raw == "-":
                    continue
                try:
                    amount = int(raw.replace(",", "").replace(".", ""))
                    break
                except ValueError:
                    continue

            if amount <= 0:
                continue

            # Classify: s2XX = fleet, s3XX = troops
            try:
                class_num = int(css_class[1:])
                if 200 <= class_num < 300:
                    fleet[unit_name] = fleet.get(unit_name, 0) + amount
                elif 300 <= class_num < 400:
                    troops[unit_name] = troops.get(unit_name, 0) + amount
            except (ValueError, IndexError):
                logger.debug("Unknown unit CSS class: %s (name=%s)", css_class, unit_name)

        return troops, fleet

    # ══════════════════════════════════════════════════════════════════
    # HELPERS  (resolve_credentials is now on BaseRunner)
    # ══════════════════════════════════════════════════════════════════
