"""
Management command: scrape unit stats from Ikariam wiki (unitdescription pages).

Usage:
    python manage.py scrape_unit_stats --ga-id <game_account_id>
    python manage.py scrape_unit_stats --ga-name BlackShadow701

Output: hub_v2/core/data/unit_stats.json
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import GameAccount
from core.encryption import decrypt

GAME_URL = "https://s{number}-{lang}.ikariam.gameforge.com/"

# Land unit IDs (Ikariam BR s78)
UNIT_IDS = list(range(301, 322))   # 301–321 — extras silently skipped on 404

# Naval unit IDs
SHIP_IDS = list(range(201, 226))   # 201–225

# Barbarian troop IDs used in the ikipedia section helpId=20
BARB_UNIT_IDS = list(range(316, 322))
BARB_SHIP_IDS = list(range(220, 226))


def _get_text(el) -> str:
    return el.get_text(strip=True) if el else ""


def _parse_precision_bar(bar_el) -> float:
    """Extract precision % from bar width style or title attribute."""
    if not bar_el:
        return 0.0
    style = bar_el.get("style", "")
    m = re.search(r"width:\s*([\d.]+)%", style)
    if m:
        return float(m.group(1))
    title = bar_el.get("title", "")
    m = re.search(r"([\d.]+)", title)
    if m:
        return float(m.group(1))
    return 0.0


def _parse_unit_html(html: str, unit_id: int, kind: str) -> dict | None:
    """Parse unitdescription HTML into a stat dict."""
    soup = BeautifulSoup(html, "html.parser")

    # Check for empty / error response
    if not soup.find("div", class_=re.compile(r"unit|soldier|ship")):
        # Try generic structure
        name_el = soup.find(class_=re.compile(r"unitName|unit_name|name"))
        if not name_el:
            # Try h3 / h2 as fallback
            name_el = soup.find("h3") or soup.find("h2")

    # Unit name — look for bold header text
    name = ""
    for sel in ["h3", "h2", ".unitName", ".name", "b"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 1:
                name = t
                break

    # Stat rows — "Pontos de vida", "Armadura", "Velocidade", "Tamanho"
    stats: dict[str, str | int | float] = {}

    # Iterate <tr> rows looking for label:value pairs
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).rstrip(":")
            value = cells[1].get_text(strip=True)
            label_lower = label.lower()
            if "vida" in label_lower or "hp" in label_lower or "life" in label_lower:
                stats["hp"] = _safe_int(value)
            elif "armadura" in label_lower or "armor" in label_lower:
                stats["armor"] = _safe_int(value)
            elif "velocidade" in label_lower or "speed" in label_lower:
                stats["speed"] = _safe_int(value)
            elif "tamanho" in label_lower or "size" in label_lower:
                stats["size"] = _safe_int(value)
            elif "munição" in label_lower or "ammo" in label_lower or "munições" in label_lower:
                stats["ammo"] = _safe_int(value)
            elif "capacidade" in label_lower or "capacity" in label_lower:
                stats["capacity"] = _safe_int(value)
            elif "exigência" in label_lower or "requisito" in label_lower:
                stats["requirement"] = value

    # Weapons — look for weapon blocks (name + dano + precisão)
    weapons = []
    # Each weapon block has a name, Dano row, Precisão row (bar)
    weapon_blocks = soup.find_all(class_=re.compile(r"weapon|attack|Weapon"))
    if not weapon_blocks:
        # fallback: look for "Dano" label pairs
        dano_rows = [r for r in soup.find_all("tr") if "Dano" in r.get_text()]
        # group by proximity — each weapon = name heading above dano rows
        wname = ""
        wdano = 0
        wprec = 0.0
        wammo = None
        for row in soup.find_all("tr"):
            txt = row.get_text(strip=True)
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).rstrip(":")
                value = cells[1].get_text(strip=True)
                llow = label.lower()
                if "dano" in llow or "damage" in llow:
                    wdano = _safe_int(value)
                elif "precisão" in llow or "precision" in llow or "accuracy" in llow:
                    bar = cells[1].find(style=re.compile(r"width"))
                    if bar:
                        wprec = _parse_precision_bar(bar)
                    else:
                        wprec = _safe_float(value)
                elif "munição" in llow or "ammo" in llow:
                    wammo = _safe_int(value)
                    weapons.append({"name": wname, "damage": wdano, "precision": wprec, "ammo": wammo})
                    wname, wdano, wprec, wammo = "", 0, 0.0, None

    # Weapon names from image alt or preceding bold/span text
    if not weapons and stats.get("hp"):
        # Build weapons from raw dano/precisão if found in page but no grouping
        pass

    # Better weapon parse: find all bold/strong labels and following data
    weapons = _parse_weapons(soup)

    result = {
        "id": unit_id,
        "kind": kind,
        "name": name,
        **stats,
        "weapons": weapons,
    }
    return result if name else None


def _parse_weapons(soup: BeautifulSoup) -> list[dict]:
    """Extract weapon blocks from the unit description page."""
    weapons = []
    current = {}

    # Walk all visible text nodes looking for weapon patterns
    all_trs = soup.find_all("tr")
    i = 0
    while i < len(all_trs):
        row = all_trs[i]
        cells = row.find_all(["td", "th"])
        if not cells:
            i += 1
            continue

        full_txt = row.get_text(separator=" ", strip=True)
        label = cells[0].get_text(strip=True).rstrip(":")
        llow = label.lower()

        # "Dano : 40" → start/continue weapon block
        if "dano" in llow or "damage" in llow:
            if current.get("name") and current.get("damage") is None:
                pass
            current["damage"] = _safe_int(cells[1].get_text(strip=True)) if len(cells) > 1 else 0

        elif "precisão" in llow or "precision" in llow or "accuracy" in llow:
            bar = None
            if len(cells) > 1:
                bar = cells[1].find(attrs={"style": re.compile(r"width")})
            current["precision"] = _parse_precision_bar(bar) if bar else _safe_float(
                cells[1].get_text(strip=True) if len(cells) > 1 else "0"
            )

        elif "munição" in llow or "ammo" in llow:
            current["ammo"] = _safe_int(cells[1].get_text(strip=True)) if len(cells) > 1 else 0
            # weapon block complete
            if current:
                weapons.append(current)
                current = {}

        elif "Precisão" in full_txt and len(weapons) == 0 and current.get("damage") is not None:
            # flush if next row would start new weapon
            weapons.append(current)
            current = {}

        i += 1

    if current and current.get("damage") is not None:
        weapons.append(current)

    # Assign weapon names from image alts or preceding headings
    # (game HTML: each weapon has an icon img before dano/precisão)
    weapon_imgs = soup.find_all("img", alt=True)
    known_weapon_names = [img["alt"] for img in weapon_imgs if img.get("alt") and len(img["alt"]) > 2]
    for idx, w in enumerate(weapons):
        if not w.get("name") and idx < len(known_weapon_names):
            w["name"] = known_weapon_names[idx]

    return weapons


def _safe_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(str(val).replace(".", "").replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(",", ".").strip())
    except (ValueError, TypeError):
        return 0.0


class Command(BaseCommand):
    help = "Scrape unit stats from Ikariam unitdescription pages and save to unit_stats.json"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--ga-id", help="GameAccount UUID")
        group.add_argument("--ga-name", help="GameAccount name (partial match)")

    def handle(self, *args, **options):
        # ── Resolve game account ──
        ga_id = options.get("ga_id")
        ga_name = options.get("ga_name")

        if ga_id:
            try:
                ga = GameAccount.objects.get(id=ga_id)
            except GameAccount.DoesNotExist:
                raise CommandError(f"GameAccount {ga_id} not found")
        else:
            qs = GameAccount.objects.filter(name__icontains=ga_name)
            if not qs.exists():
                raise CommandError(f"No GameAccount matching '{ga_name}'")
            ga = qs.first()

        self.stdout.write(f"Account: {ga.name} / {ga.server_id}")

        # ── Decrypt session cookies ──
        if not ga.session_cookies_enc:
            raise CommandError("No session cookies — run CheckStatus first to login")

        try:
            cookies_json = decrypt(ga.session_cookies_enc)
            cookies: dict = json.loads(cookies_json)
        except Exception as e:
            raise CommandError(f"Failed to decrypt session: {e}")

        self.stdout.write(f"Session cookies: {len(cookies)} keys")

        # ── Build requests session ──
        sess = requests.Session()
        sess.cookies.update(cookies)
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
        })

        base = GAME_URL.format(number=ga.server_number, lang=ga.server_language)
        self.stdout.write(f"Base URL: {base}")

        # Get actionRequest from homepage
        action_request, session_ok = self._get_action_request(sess, base)
        self.stdout.write(f"actionRequest: {action_request[:16]}… | session_ok={session_ok}")
        if not session_ok:
            raise CommandError("Session expired or invalid — run CheckStatus job first to refresh")

        # ── Scrape units ──
        unit_stats: dict[str, dict] = {}

        self.stdout.write("\n--- Land Units ---")
        for uid in UNIT_IDS:
            data = self._fetch_unit(sess, base, action_request, uid=uid)
            if data:
                unit_stats[str(uid)] = data
                self.stdout.write(f"  {uid}: {data.get('name','?')}  HP={data.get('hp')} armor={data.get('armor')} speed={data.get('speed')} size={data.get('size')}")
            time.sleep(0.8)

        self.stdout.write("\n--- Naval Units ---")
        for sid in SHIP_IDS:
            data = self._fetch_unit(sess, base, action_request, ship_id=sid)
            if data:
                unit_stats[str(sid)] = data
                self.stdout.write(f"  {sid}: {data.get('name','?')}  HP={data.get('hp')} armor={data.get('armor')} speed={data.get('speed')}")
            time.sleep(0.8)

        # ── Save ──
        # Inside container: /app/apps/game/management/commands/... → parents[4] = /app
        out_dir = Path(__file__).resolve().parents[4] / "core" / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "unit_stats.json"
        out_path.write_text(json.dumps(unit_stats, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"\nSaved {len(unit_stats)} units → {out_path}"))

    def _get_action_request(self, sess: requests.Session, base: str) -> tuple[str, bool]:
        try:
            resp = sess.get(base, timeout=20)
            # Check if still logged in (game homepage contains cityId or logout link)
            logged_in = "index.php?logout" in resp.text or "cityId" in resp.text
            m = re.search(r'actionRequest["\s:=]+([a-f0-9]{32})', resp.text)
            if m:
                return m.group(1), logged_in
            return "00000000000000000000000000000000", logged_in
        except Exception as e:
            self.stderr.write(f"Homepage fetch error: {e}")
            return "00000000000000000000000000000000", False

    def _fetch_unit(
        self, sess: requests.Session, base: str, ar: str,
        uid: int | None = None, ship_id: int | None = None
    ) -> dict | None:
        if uid is not None:
            params = {
                "view": "unitdescription",
                "unitId": uid,
                "helpId": 9,
                "subHelpId": 0,
                "templateView": "unitdescription",
                "actionRequest": ar,
                "ajax": 1,
            }
            kind = "land"
            entity_id = uid
        else:
            params = {
                "view": "unitdescription",
                "shipId": ship_id,
                "helpId": 10,
                "subHelpId": 0,
                "templateView": "unitdescription",
                "actionRequest": ar,
                "ajax": 1,
            }
            kind = "naval"
            entity_id = ship_id

        try:
            resp = sess.get(base, params=params, timeout=20)
            if resp.status_code == 404 or not resp.text.strip():
                return None
            html = resp.text
            # Empty / "not found" response
            if len(html) < 200:
                return None
            return _parse_unit_html(html, entity_id, kind)
        except Exception as e:
            self.stderr.write(f"  Error fetching {entity_id}: {e}")
            return None
