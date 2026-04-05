"""Helpers for reading island donation state and production sliders."""

from __future__ import annotations

import json
import re
import time
from typing import Any


CITY_JSON_PATTERN = re.compile(
    r'"updateBackgroundData",\s?([\s\S]*?)\],\s*\["updateTemplateData"'
)
LEVEL_PATTERN = re.compile(r'<p class="headline_huge bold">\s*([^<]+)\s*</p>')
NEXT_LEVEL_PATTERN = re.compile(r"Pr[oó]ximo n[ií]vel:\s*([^<]+)</h4>", re.IGNORECASE)
PROGRESS_BAR_PATTERN = re.compile(r'id="upgradeProgress"[^>]*title="(\d+)%"')
COUNTDOWN_PATTERN = re.compile(r'id="upgradeCountDown">\s*([^<]+)\s*</div>')
WOOD_VALUE_PATTERN = re.compile(r'<li class="wood">(.*?)</li>')


def clean_number(raw: str | int | float | None) -> int:
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    return int(digits or "0")


def extract_city_data(html: str) -> dict[str, Any]:
    match = CITY_JSON_PATTERN.search(html)
    if not match:
        raise ValueError("city_payload_not_found")
    return json.loads(match.group(1), strict=False)


def fetch_city_context(client, city_id: int) -> dict[str, Any]:
    resp = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
    city = extract_city_data(resp.text)
    island_id = str(city.get("islandId") or "")
    if not island_id:
        raise ValueError(f"island_not_found:{city_id}")
    return {
        "city_id": str(city_id),
        "city_name": str(city.get("name") or city_id),
        "island_id": island_id,
        "tradegood_type": str(city.get("tradegood") or ""),
    }


def fetch_donation_project_state(client, city_id: int, donation_type: str) -> dict[str, Any]:
    context = fetch_city_context(client, city_id)
    view = "resource" if donation_type == "resource" else "tradegood"
    raw_type = "resource" if donation_type == "resource" else context.get("tradegood_type") or "1"
    resp = client._request(
        "POST",
        client._server_url,
        data={
            "view": view,
            "type": raw_type,
            "islandId": context["island_id"],
            "cityId": str(city_id),
            "backgroundView": "island",
            "currentIslandId": context["island_id"],
            "actionRequest": client._action_request,
            "ajax": "1",
        },
    )
    payload = resp.json()
    background = payload[0][1]["backgroundData"]
    html = payload[1][1][1]

    level_match = LEVEL_PATTERN.search(html)
    next_level_match = NEXT_LEVEL_PATTERN.search(html)
    values = [clean_number(value) for value in WOOD_VALUE_PATTERN.findall(html)]

    end_key = "resourceEndUpgradeTime" if donation_type == "resource" else "tradegoodEndUpgradeTime"
    finish_at_ts = int(background.get(end_key) or 0)
    now_ts = int(time.time())

    return {
        **context,
        "view": view,
        "page_type": raw_type,
        "level": clean_number(level_match.group(1)) if level_match else clean_number(background.get("resourceLevel" if donation_type == "resource" else "tradegoodLevel")),
        "next_level": clean_number(next_level_match.group(1)) if next_level_match else 0,
        "required": values[0] if len(values) >= 1 else 0,
        "present": values[1] if len(values) >= 2 else 0,
        "remaining": max(0, (values[0] if len(values) >= 1 else 0) - (values[1] if len(values) >= 2 else 0)),
        "free_remaining": values[2] if len(values) >= 3 else 0,
        "is_upgrading": "Em progresso!" in html or finish_at_ts > now_ts,
        "finish_at_ts": finish_at_ts,
        "seconds_until_finish": max(0, finish_at_ts - now_ts) if finish_at_ts else 0,
        "progress_percent": clean_number(PROGRESS_BAR_PATTERN.search(html).group(1)) if PROGRESS_BAR_PATTERN.search(html) else 0,
        "countdown_text": COUNTDOWN_PATTERN.search(html).group(1).strip() if COUNTDOWN_PATTERN.search(html) else "",
    }


def fetch_worker_baseline(client, city_id: int) -> dict[str, Any]:
    context = fetch_city_context(client, city_id)

    def _slider(resource_type: str, page_type: str) -> dict[str, int]:
        resp = client._request(
            "POST",
            client._server_url,
            data={
                "view": resource_type,
                "type": page_type,
                "islandId": context["island_id"],
                "cityId": str(city_id),
                "backgroundView": "island",
                "currentIslandId": context["island_id"],
                "actionRequest": client._action_request,
                "ajax": "1",
            },
        )
        payload = resp.json()
        slider = payload[2][1]["js_ResourceSlider"]["slider"]
        callback = slider.get("callback_data") or {}
        max_workers = int(slider.get("max_value", callback.get("max_value", 0)) or 0)
        current_workers = int(slider.get("ini_value", callback.get("ini_workers", 0)) or 0)
        percent = 0
        if max_workers > 0:
            percent = max(0, min(150, int(round((current_workers / max_workers) * 100))))
        return {
            "current_workers": current_workers,
            "max_workers": max_workers,
            "percent": percent,
        }

    resource = _slider("resource", "resource")
    luxury = _slider("tradegood", context.get("tradegood_type") or "1")
    return {
        **context,
        "resource": resource,
        "tradegood": luxury,
    }


def fetch_global_gold(client) -> int:
    resp = client._request("GET", client._server_url, params={"view": "updateGlobalData", "ajax": "1"})
    payload = json.loads(resp.text, strict=False)
    header = payload[0][1]["headerData"]
    return clean_number(header.get("gold"))
