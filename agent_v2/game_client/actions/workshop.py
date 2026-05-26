"""Workshop (Oficina do Inventor) actions — query state and start unit improvements."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        text = re.sub(r"[^\d-]", "", str(raw or "").strip())
        return int(text or default)
    except Exception:
        return default


def _clean_text(raw: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    text = text.replace("&nbsp;", " ").replace("\\/", "/").replace("\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_duration_seconds(raw: str) -> int:
    """Parse Ikariam duration strings like '1D 02:30:45' or '1h 30m 15s'."""
    text = _clean_text(raw).lower()
    if not text:
        return 0

    # Format: days:hh:mm:ss or hh:mm:ss or mm:ss
    colon_match = re.match(r"(?:(\d+)d\s*)?(\d+):(\d+):(\d+)", text)
    if colon_match:
        days = _to_int(colon_match.group(1))
        hours = _to_int(colon_match.group(2))
        minutes = _to_int(colon_match.group(3))
        seconds = _to_int(colon_match.group(4))
        return days * 86400 + hours * 3600 + minutes * 60 + seconds

    # Format: "1d 2h 30m 15s" style
    total = 0
    for pattern, factor in (
        (r"(\d+)\s*d", 86400),
        (r"(\d+)\s*h", 3600),
        (r"(\d+)\s*m(?!s)", 60),
        (r"(\d+)\s*s", 1),
    ):
        found = re.search(pattern, text)
        if found:
            total += _to_int(found.group(1)) * factor
    return total


def _now_utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _coerce_remaining_seconds(end_time: int, current_time: int) -> int:
    """Normalize workshop timers to a remaining-seconds delta.

    Some servers send absolute Unix timestamps, others may already send deltas.
    We treat 10-digit values as timestamps and smaller values as deltas.
    """
    end_time = int(end_time or 0)
    current_time = int(current_time or 0)
    if end_time <= 0:
        return 0
    if end_time >= 1_000_000_000:
        reference = current_time if current_time > 0 else _now_utc_ts()
        return max(0, end_time - reference)
    return max(0, end_time)


class WorkshopAction(BaseAction):
    """Interact with the Workshop building (Oficina do Inventor).

    The Workshop lets players research unit improvements that permanently boost
    a specific unit type in a city.  One improvement can be in progress per
    city at a time; each costs gold and optionally crystal.

    HTTP parameter notes (derived from live Ikariam AJAX traffic):
      GET state : view=workshop, activeTab=tabUnits
      GET start : action=StartWorkshopUpgrade, unitId=<id>, upgradeType=<offensive|defensive>

    Older servers may still serve the legacy ``workshopScreen`` payload, so the
    parser keeps a fallback for that variant.
    """

    _VIEW = "workshop"
    _LEGACY_VIEW = "workshopScreen"
    _ACTION = "StartWorkshopUpgrade"
    _LEGACY_ACTION = "WorkshopScreen"
    _LEGACY_START_FUNCTION = "startWorkshopResearch"

    # ── Public API ──

    def get_state(self, *, city_id: int, position: int, **kwargs: Any) -> dict[str, Any]:
        """Fetch the current workshop state for a city (both land and naval tabs).

        Returns a dict with:
          - ``in_progress``: bool — whether a research is currently running
          - ``remaining_seconds``: int — seconds left on the current research (0 if idle)
          - ``remaining_text``: str  — human-readable remaining time
          - ``improvements``: list[dict] — available improvements (land + naval merged)
              Each entry: {id, name, gold_cost, crystal_cost, duration_seconds, duration_text}
          - ``gold``: int — current gold in the account header
          - ``updated_at``: str — ISO timestamp
        """
        base_params = {
            "view": self._VIEW,
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        try:
            # Fetch land unit improvements
            land_resp = self.client._request(
                "GET", self.client._server_url,
                params={**base_params, "activeTab": "tabUnits"},
                headers=GAME_AJAX_HEADERS,
            )
            land_state = self._parse_state_payload(land_resp.json())

            # Fetch naval unit improvements
            try:
                fleet_resp = self.client._request(
                    "GET", self.client._server_url,
                    params={**base_params, "activeTab": "tabShips"},
                    headers=GAME_AJAX_HEADERS,
                )
                fleet_state = self._parse_state_payload(fleet_resp.json())
                # Merge: use whichever tab shows in_progress; combine improvement lists
                if fleet_state.get("in_progress"):
                    land_state["in_progress"] = True
                    land_state["remaining_seconds"] = fleet_state["remaining_seconds"]
                    land_state["remaining_text"] = fleet_state["remaining_text"]
                seen_ids = {imp["id"] for imp in land_state.get("improvements") or []}
                for imp in fleet_state.get("improvements") or []:
                    if imp.get("id") not in seen_ids:
                        land_state.setdefault("improvements", []).append(imp)
            except Exception:
                pass  # fleet tab unavailable or parse error — use land-only result

            return land_state
        except Exception:
            pass

        # Legacy fallback for older server variants still using workshopScreen.
        legacy_params = {
            "view": self._LEGACY_VIEW,
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": self._LEGACY_VIEW,
            "actionRequest": self.client._action_request,
        }
        resp = self.client._request("GET", self.client._server_url, params=legacy_params)
        html = resp.text

        token_match = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', html)
        if token_match:
            self.client._action_request = token_match.group(1)

        gold_match = re.search(r'"gold"\s*:\s*"?([\d.]+)"?', html)
        gold = _to_int(gold_match.group(1)) if gold_match else 0

        if "startWorkshopResearch" in html or "improveUnit" in html or "js_WorkshopResearch" in html:
            return self._parse_html(html, gold)

        legacy_params["ajax"] = "1"
        resp2 = self.client._request("GET", self.client._server_url, params=legacy_params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp2.json()
        except Exception as exc:
            raise ActionError("Invalid workshop response", action="workshop_get_state") from exc
        return self._parse_state_payload(payload)

    def start_improvement(
        self,
        *,
        city_id: int,
        position: int,
        improvement_id: int,
        upgrade_type: str = "offensive",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Submit a workshop improvement research request.

        Returns a dict with ``ok`` and updated ``gold`` (if parseable from response).
        Raises ActionError if the server rejects the request.
        """
        # Naval unit IDs are in the 200-range; land units are 300+
        active_tab = "tabShips" if int(improvement_id) < 300 else "tabUnits"
        params = {
            "action": self._ACTION,
            "cityId": str(city_id),
            "position": str(position),
            "unitId": str(improvement_id),
            "upgradeType": str(upgrade_type or "offensive"),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "activeTab": active_tab,
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        try:
            resp = self.client._request("GET", self.client._server_url, params=params, headers=GAME_AJAX_HEADERS)
            return self._parse_action_payload(resp)
        except Exception:
            legacy_params = {
                "action": self._LEGACY_ACTION,
                "function": self._LEGACY_START_FUNCTION,
                "cityId": str(city_id),
                "position": str(position),
                "researchId": str(improvement_id),
                "backgroundView": "city",
                "currentCityId": str(city_id),
                "templateView": self._LEGACY_VIEW,
                "actionRequest": self.client._action_request,
                "ajax": "1",
            }
            resp = self.client._request("POST", self.client._server_url, data=legacy_params, headers=GAME_AJAX_HEADERS)
            return self._parse_action_payload(resp)

    # ── Parsing helpers ──

    @staticmethod
    def _normalize(payload: Any) -> list[Any]:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
            first = payload[0]
            if first and isinstance(first[0], list):
                return first
        return payload if isinstance(payload, list) else []

    def _parse_action_payload(self, resp) -> dict[str, Any]:
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True}
        updates = self._normalize(payload)
        gold = 0
        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
                header = item[1].get("headerData") or {}
                gold = _to_int(header.get("gold") or gold, gold)
        return {"ok": True, "gold": gold}

    def _parse_state_payload(self, payload: Any) -> dict[str, Any]:
        updates = self._normalize(payload)
        html = ""
        gold = 0
        template_data: dict[str, Any] = {}
        server_time = 0

        # Log all top-level keys for diagnostics
        found_keys: list[str] = []
        for item in updates:
            if not isinstance(item, list) or len(item) < 1:
                continue
            found_keys.append(str(item[0]))

        if found_keys:
            logger.debug("WorkshopAction: response keys=%s", found_keys)

        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = item[0]
            data = item[1]

            if name == "updateGlobalData" and isinstance(data, dict):
                token = str(data.get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
                header = data.get("headerData") or {}
                gold = _to_int(header.get("gold") or 0, 0)
                server_time = _to_int(data.get("time") or server_time, server_time)

            elif name == "changeView":
                # data may be a list [viewName, html] or just a string
                if isinstance(data, list) and len(data) >= 2:
                    html = str(data[1] or "")
                elif isinstance(data, str):
                    html = data

            elif name == "updateTemplateData":
                if isinstance(data, dict):
                    template_data = data
                elif isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
                    template_data = data[1]
                elif isinstance(data, str) and data.strip():
                    html = data
                elif isinstance(data, list) and len(data) >= 2 and isinstance(data[1], str) and data[1].strip():
                    html = data[1]

            elif name == "popupData":
                # Modern Ikariam serves workshop as a popup — data may be HTML string or list
                popup_html = ""
                if isinstance(data, str) and data.strip():
                    popup_html = data
                elif data is None:
                    popup_html = ""
                elif isinstance(data, list):
                    for part in data:
                        if isinstance(part, str) and len(part) > 50:
                            popup_html = part
                            break
                        if isinstance(part, list) and len(part) >= 2 and isinstance(part[1], str) and len(part[1]) > 50:
                            popup_html = part[1]
                            break
                if popup_html and not html:
                    html = popup_html
                if data is not None and not popup_html:
                    try:
                        snippet = json.dumps(data, ensure_ascii=False)[:800]
                    except Exception:
                        snippet = str(data)[:800]
                    logger.warning("WorkshopAction: popupData unrecognized structure: %s", snippet)

        # Primary: try structured template data (present on some server versions)
        if template_data:
            result = self._parse_template_data(template_data, gold, server_time=server_time)
            if result is not None:
                return result

        # Fallback: parse HTML
        if html:
            return self._parse_html(html, gold)

        # Last resort: log the raw payload for diagnosis
        try:
            full = json.dumps(payload, ensure_ascii=False)
        except Exception:
            full = str(payload)
        logger.warning(
            "WorkshopAction: no usable data. keys=%s full_response=%s",
            found_keys, full,
        )
        raise ActionError(
            f"Workshop response has no usable data. keys={found_keys}",
            action="workshop_get_state",
        )

    def _parse_template_data(
        self,
        data: dict[str, Any],
        gold: int,
        server_time: int = 0,
    ) -> dict[str, Any] | None:
        """Parse structured templateData returned by some Ikariam server versions.

        Expected structure (approximate):
            {
              "inProgress": {
                "endTime": 1700000000,   // Unix timestamp
                "currentdate": 1699999000
              },
              "researchList": [
                {
                  "id": 42,
                  "name": "Hoplita: Ataque Nivel 3",
                  "goldCost": 5000,
                  "crystalCost": 2000,
                  "researchTime": 3600   // seconds
                },
                ...
              ]
            }
        """
        in_progress_data = data.get("inProgress")
        research_list = data.get("researchList") or []
        complete_data = data.get("completeData") or {}
        unit_details = data.get("unitDetails") or {}

        if not in_progress_data and not research_list and not complete_data:
            return None  # empty template data — fall through to HTML

        in_progress = False
        remaining_seconds = 0
        remaining_text = ""

        if in_progress_data and isinstance(in_progress_data, dict):
            end_time = _to_int(in_progress_data.get("endTime") or in_progress_data.get("endtime") or 0)
            current_date = _to_int(
                in_progress_data.get("currentdate")
                or in_progress_data.get("currentDate")
                or in_progress_data.get("servertime")
                or 0
            )
            if end_time > 0:
                in_progress = True
                reference_time = current_date or server_time
                remaining_seconds = _coerce_remaining_seconds(end_time, reference_time)
                remaining_text = _duration_human(remaining_seconds)
            else:
                raw_remaining = str(
                    in_progress_data.get("duration")
                    or in_progress_data.get("remaining")
                    or in_progress_data.get("remainingTime")
                    or ""
                ).strip()
                if raw_remaining:
                    in_progress = True
                    if raw_remaining.isdigit() and len(raw_remaining) >= 9:
                        remaining_seconds = _coerce_remaining_seconds(_to_int(raw_remaining), current_date or server_time)
                    else:
                        remaining_seconds = _parse_duration_seconds(raw_remaining)
                    remaining_text = raw_remaining

        improvements = []
        if isinstance(complete_data, dict):
            for unit_id_raw, upgrade_data in complete_data.items():
                unit_id = _to_int(unit_id_raw, 0)
                if unit_id <= 0 or not isinstance(upgrade_data, dict):
                    continue
                unit_meta = unit_details.get(str(unit_id)) or unit_details.get(unit_id) or {}
                unit_name = _clean_text(unit_meta.get("unitName") or f"Unidade #{unit_id}")

                for upgrade_type in ("offensive", "defensive"):
                    branch = upgrade_data.get(upgrade_type) or {}
                    if not isinstance(branch, dict):
                        continue
                    next_level = branch.get("nextLevel") or {}
                    current_level = branch.get("currentLevel") or {}
                    if not isinstance(next_level, dict) or not next_level:
                        continue
                    if branch.get("errorText"):
                        continue

                    current_lv = _to_int(current_level.get("upgradeLevel"), 0)
                    next_lv = _to_int(next_level.get("upgradeLevel"), current_lv + 1)
                    current_bonus = _to_int(current_level.get("upgradeEffect"), 0)
                    next_bonus = _to_int(next_level.get("upgradeEffect"), 0)
                    bonus_per_level = (next_bonus - current_bonus) if next_bonus > current_bonus else 0
                    duration_text = _clean_text(next_level.get("duration") or "")
                    improvements.append({
                        "id": unit_id,
                        "unit_id": unit_id,
                        "unit_name": unit_name,
                        "upgrade_type": upgrade_type,
                        "upgrade_type_desc": _clean_text(branch.get("upgradeTypeDesc") or ""),
                        "upgrade_name": _clean_text(next_level.get("upgradeName") or ""),
                        "name": f"{unit_name} - {branch.get('upgradeTypeName') or upgrade_type} Nivel {next_lv}",
                        "current_level": current_lv,
                        "next_level": next_lv,
                        "current_bonus": current_bonus,
                        "next_bonus": next_bonus,
                        "bonus_per_level": bonus_per_level,
                        "gold_cost": _to_int(next_level.get("goldCosts") or next_level.get("goldCostsShortened") or 0),
                        "crystal_cost": _to_int(next_level.get("crystalCosts") or next_level.get("crystalCostsShortened") or 0),
                        "duration_seconds": _parse_duration_seconds(duration_text),
                        "duration_text": duration_text,
                    })

        if isinstance(research_list, list):
            for entry in research_list:
                if not isinstance(entry, dict):
                    continue
                imp_id = _to_int(entry.get("id") or entry.get("researchId") or 0)
                if imp_id <= 0:
                    continue
                duration_raw = _to_int(entry.get("researchTime") or entry.get("duration") or 0)
                improvements.append({
                    "id": imp_id,
                    "name": _clean_text(entry.get("name") or entry.get("unitName") or f"Melhoria #{imp_id}"),
                    "gold_cost": _to_int(entry.get("goldCost") or entry.get("gold_cost") or 0),
                    "crystal_cost": _to_int(entry.get("crystalCost") or entry.get("crystal_cost") or 0),
                    "duration_seconds": duration_raw,
                    "duration_text": _duration_human(duration_raw) if duration_raw else "",
                })

        return {
            "in_progress": in_progress,
            "remaining_seconds": remaining_seconds,
            "remaining_text": remaining_text,
            "improvements": improvements,
            "gold": gold,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _parse_html(html: str, gold: int) -> dict[str, Any]:
        """Extract workshop state from the workshop screen HTML (fallback).

        Handles multiple Ikariam server-version HTML patterns for timers and
        improvement lists.
        """
        # ── Check for active research ──
        in_progress = False
        remaining_seconds = 0
        remaining_text = ""

        timer_patterns = [
            r"id=['\"]js_Timer[^'\"]*['\"][^>]*>([^<]+)<",
            r"class=['\"][^'\"]*remainingTime[^'\"]*['\"][^>]*>([^<]+)<",
            r"Tempo\s+restante[^:]*:\s*<[^>]*>([^<]+)<",
            r"Conclu[íi]do\s+em[^:]*:\s*([^<]+?)(?:<|$)",
            r"Pesquisa\s+conclu[íi]da\s+em[^:]*:\s*([^<]+?)(?:<|$)",
        ]
        for pattern in timer_patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                raw_time = _clean_text(m.group(1))
                secs = _parse_duration_seconds(raw_time)
                if secs > 0:
                    in_progress = True
                    remaining_seconds = secs
                    remaining_text = raw_time
                    break

        if not in_progress:
            busy_markers = [
                "js_WorkshopResearch",
                "currentImprovement",
                "researchInProgress",
            ]
            for marker in busy_markers:
                if marker in html:
                    in_progress = True
                    break

        # ── Extract available improvements ──
        # Legacy variants:
        #   onclick="startWorkshopResearch(<id>)"  or improveUnit(<id>)
        improvements: list[dict[str, Any]] = []

        for m in re.finditer(
            r"(?:startWorkshopResearch|improveUnit)\((\d+)(?:,\s*(\d+))?\)",
            html,
            re.IGNORECASE,
        ):
            if m.group(2) is not None:
                improvement_id = _to_int(m.group(2))
            else:
                improvement_id = _to_int(m.group(1))

            if improvement_id <= 0:
                continue

            start = max(0, m.start() - 600)
            context = html[start:m.start()]

            name = _extract_improvement_name(context)
            gold_cost = _extract_gold_cost(context)
            crystal_cost = _extract_crystal_cost(context)
            duration_text, duration_seconds = _extract_duration(context)

            improvements.append({
                "id": improvement_id,
                "name": name,
                "gold_cost": gold_cost,
                "crystal_cost": crystal_cost,
                "duration_seconds": duration_seconds,
                "duration_text": duration_text,
            })

        # Deduplicate by improvement_id
        seen_ids: set[int] = set()
        unique_improvements: list[dict[str, Any]] = []
        for imp in improvements:
            if imp["id"] not in seen_ids:
                seen_ids.add(imp["id"])
                unique_improvements.append(imp)

        # Modern variants render direct StartWorkshopUpgrade links in the HTML.
        modern_improvements = _extract_modern_improvements(html)
        if modern_improvements:
            existing = {(imp.get("id"), imp.get("upgrade_type")) for imp in unique_improvements}
            for imp in modern_improvements:
                key = (imp.get("id"), imp.get("upgrade_type"))
                if key not in existing:
                    existing.add(key)
                    unique_improvements.append(imp)

        return {
            "in_progress": in_progress,
            "remaining_seconds": remaining_seconds,
            "remaining_text": remaining_text,
            "improvements": unique_improvements,
            "gold": gold,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _duration_human(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}min" if s == 0 else f"{m}min {s}s"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h" if m == 0 else f"{h}h {m}min"
    d, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{d}d" if h == 0 else f"{d}d {h}h"


def _extract_improvement_name(context: str) -> str:
    patterns = [
        r"class=['\"][^'\"]*improvement[Nn]ame[^'\"]*['\"][^>]*>([^<]{3,80})<",
        r"class=['\"][^'\"]*unitName[^'\"]*['\"][^>]*>([^<]{3,80})<",
        r"class=['\"][^'\"]*researchName[^'\"]*['\"][^>]*>([^<]{3,80})<",
        r"title=['\"]([^'\"]{3,80})['\"]",
    ]
    for pattern in patterns:
        m = re.search(pattern, context, re.IGNORECASE)
        if m:
            return _clean_text(m.group(1))
    return ""


def _extract_gold_cost(context: str) -> int:
    patterns = [
        r"(\d[\d.,]*)\s*(?:Ouro|Gold|ouro|gold)",
        r"(?:Ouro|Gold|ouro|gold)[^0-9]{0,20}(\d[\d.,]*)",
        r"class=['\"][^'\"]*(?:cost|custo|gold)[^'\"]*['\"][^>]*>(?:[^<]*<[^>]+>)*([^<]{1,30})<",
    ]
    for pattern in patterns:
        m = re.search(pattern, context, re.IGNORECASE)
        if m:
            raw = re.sub(r"[.,]", "", m.group(1).strip())
            value = _to_int(raw)
            if value > 0:
                return value
    return 0


def _extract_crystal_cost(context: str) -> int:
    patterns = [
        r"(\d[\d.,]*)\s*(?:Cristal|Crystal|cristal|crystal|vidro)",
        r"(?:Cristal|Crystal|cristal|crystal)[^0-9]{0,20}(\d[\d.,]*)",
    ]
    for pattern in patterns:
        m = re.search(pattern, context, re.IGNORECASE)
        if m:
            raw = re.sub(r"[.,]", "", m.group(1).strip())
            value = _to_int(raw)
            if value > 0:
                return value
    return 0


def _extract_duration(context: str) -> tuple[str, int]:
    patterns = [
        r"class=['\"][^'\"]*duration[^'\"]*['\"][^>]*>([^<]{2,40})<",
        r"class=['\"][^'\"]*time[^'\"]*['\"][^>]*>([^<]{2,40})<",
        r"(\d+:\d+:\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, context, re.IGNORECASE)
        if m:
            raw = _clean_text(m.group(1))
            secs = _parse_duration_seconds(raw)
            if secs > 0:
                return raw, secs
    return "", 0


def _extract_modern_improvements(html: str) -> list[dict[str, Any]]:
    improvements: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<div class=\\"unitDisplay[^>]*title=\\"([^\\"]+)\\".*?'
        r'href=\\"\?action=StartWorkshopUpgrade&cityId=\d+&position=\d+&unitId=(\d+)&upgradeType=(offensive|defensive)\\"'
        r'.*?title=\\"([^\\"]+)\\"[^>]*>Melhorar<\\/a>'
        r'.*?Pr[oó]ximo n[íi]vel:\s*([^<(]+)\s*\((\d+)\)'
        r'.*?(?:Dano|Armadura)\s*\+?(\d+)\s*&DoubleRightArrow;\s*\+?(\d+)'
        r'.*?title=\\"Custos:\s*([\d.]+)\s*Ouro\\">'
        r'.*?title=\\"Custos:\s*([\d.]+)\s*Cristal\\">'
        r'.*?title=\\"Dura[cç][aã]o:\s*([^\\"]+)\\"',
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        unit_name = _clean_text(match.group(1))
        unit_id = _to_int(match.group(2), 0)
        upgrade_type = _clean_text(match.group(3)).lower() or "offensive"
        upgrade_type_name = _clean_text(match.group(4))
        upgrade_name = _clean_text(match.group(5))
        next_level = _to_int(match.group(6), 0)
        current_effect = _to_int(match.group(7), 0)
        next_effect = _to_int(match.group(8), 0)
        gold_cost = _to_int(match.group(9), 0)
        crystal_cost = _to_int(match.group(10), 0)
        duration_text = _clean_text(match.group(11))
        if unit_id <= 0:
            continue
        inferred_current = max(0, int(current_effect / 5)) if current_effect > 0 else max(0, next_level - 1)
        # bonus_per_level: derive increment from two consecutive cumulative values
        bonus_per_level = (next_effect - current_effect) if (next_effect > 0 and current_effect >= 0 and next_effect > current_effect) else 0
        improvements.append({
            "id": unit_id,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "upgrade_type": upgrade_type,
            "name": f"{unit_name} - {upgrade_type_name} Nivel {next_level}",
            "upgrade_name": upgrade_name,
            "current_level": inferred_current,
            "next_level": next_level,
            "current_bonus": current_effect,
            "next_bonus": next_effect,
            "bonus_per_level": bonus_per_level,
            "gold_cost": gold_cost,
            "crystal_cost": crystal_cost,
            "duration_seconds": _parse_duration_seconds(duration_text),
            "duration_text": duration_text,
        })
    return improvements
