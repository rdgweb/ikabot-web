"""Quick script to verify ika-tools raw data extraction."""
import json
import re
import sys

import requests


IKA_TOOLS_URL = "https://ika-tools.com/"


def _parse_int(raw, default=0):
    try:
        return int(float(str(raw or "").strip()))
    except Exception:
        return default


def _seconds_from_duration(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return 0
    total = 0
    for token in text.split():
        if token.endswith("s"):
            total += _parse_int(token[:-1])
        elif token.endswith("m"):
            total += _parse_int(token[:-1]) * 60
        elif token.endswith("h"):
            total += _parse_int(token[:-1]) * 3600
        elif token.endswith("D"):
            total += _parse_int(token[:-1]) * 86400
    return total


def _format_duration(seconds):
    if seconds <= 0:
        return "-"
    parts = []
    remaining = int(seconds)
    for suffix, scale in [("D", 86400), ("h", 3600), ("m", 60), ("s", 1)]:
        if remaining < scale:
            continue
        amount = remaining // scale
        remaining %= scale
        parts.append(f"{amount}{suffix}")
        if len(parts) >= 3:
            break
    return " ".join(parts) if parts else "0s"


def _extract_js_array(source, var_name):
    marker = f"{var_name}=["
    start = source.find(marker)
    if start < 0:
        return None
    i = start + len(marker)
    depth = 1
    in_string = False
    quote_char = ""
    escaped = False
    chunks = []
    while i < len(source):
        ch = source[i]
        if in_string:
            chunks.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_char:
                in_string = False
        else:
            if ch in ("'", '"'):
                in_string = True
                quote_char = ch
                chunks.append(ch)
            elif ch == "[":
                depth += 1
                chunks.append(ch)
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return "".join(chunks)
                chunks.append(ch)
            else:
                chunks.append(ch)
        i += 1
    return None


def _js_object_array_to_python(raw):
    jsonish = re.sub(r"([{,])([A-Za-z_][A-Za-z0-9_]*):", r'\1"\2":', raw)
    jsonish = re.sub(
        r"(?<=:|\[|,)\s*0x[0-9A-Fa-f]+",
        lambda m: str(int(m.group(0).strip(), 16)),
        jsonish,
    )
    return json.loads(f"[{jsonish}]")


def fetch_tables():
    print("Buscando ika-tools.com...")
    home = requests.get(IKA_TOOLS_URL, timeout=20)
    home.raise_for_status()
    match = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
    if not match:
        raise RuntimeError("bundle não encontrado")
    bundle_url = f"{IKA_TOOLS_URL}assets/{match.group(1)}"
    print(f"Bundle: {bundle_url}")
    js = requests.get(bundle_url, timeout=30)
    js.raise_for_status()
    source = js.text

    mapping_match = re.search(r"const rl=\{(.*?)\},E1=", source, re.DOTALL)
    if not mapping_match:
        raise RuntimeError("mapeamento não encontrado")

    tables = {}
    for m in re.finditer(r"([A-Za-z0-9_]+):([A-Za-z0-9_]+)", mapping_match.group(1)):
        slug = m.group(1).strip()
        var_name = m.group(2).strip()
        raw_array = _extract_js_array(source, var_name)
        if not raw_array:
            match2 = re.search(
                rf"\b{re.escape(var_name)}=JSON\.parse\('((?:\\.|[^'])*)'\)", source
            )
            if match2:
                raw = match2.group(1).strip()
                if raw.startswith("[") and raw.endswith("]"):
                    raw_array = raw[1:-1]
        if not raw_array:
            continue
        try:
            inner = raw_array.lstrip()
            if inner.startswith("["):
                inner = inner[1:]
            tables[slug] = _js_object_array_to_python(inner)
        except Exception:
            continue

    return tables


def show_building(tables, slug, levels=8):
    rows = tables.get(slug)
    if not rows:
        print(f"  {slug}: NAO ENCONTRADO na tabela")
        print(f"  Chaves disponiveis (amostra): {sorted(tables.keys())[:20]}")
        return
    print(f"  Campos: {list(rows[0].keys()) if rows else []}")
    for row in rows[:levels]:
        lvl = row.get("level")
        bt = row.get("building_time")
        secs = _seconds_from_duration(str(bt or ""))
        wood = row.get("wood") or row.get("resource") or "-"
        wine = row.get("wine") or "-"
        marble = row.get("marble") or "-"
        print(f"  nivel {lvl:>2}: time={repr(bt):<14} ({secs:>6}s = {_format_duration(secs):<10})  madeira={wood}  vinho={wine}  marmore={marble}")


if __name__ == "__main__":
    tables = fetch_tables()
    print(f"\nTotal de tabelas extraidas: {len(tables)}")
    print(f"Slugs: {sorted(tables.keys())}\n")

    for building in ["governorsResidence", "townHall", "warehouse"]:
        print(f"=== {building} ===")
        show_building(tables, building)
        print()
