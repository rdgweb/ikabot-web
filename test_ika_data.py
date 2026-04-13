"""Verify ika-tools data for specific buildings."""
import re
import json
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


def _adjusted_time(seconds, world_pct, government_pct):
    v = float(seconds)
    v *= 1 - (world_pct / 100.0)
    v *= 1 - (government_pct / 100.0)
    return int(v + 0.9999)


def extract_raw_array(source, var_name):
    """Extract a JS array assigned to var_name."""
    marker = f"{var_name}=["
    start = source.find(marker)
    if start < 0:
        return None
    # scan for matching closing bracket
    chunks = []
    depth = 0
    in_string = False
    quote_char = ""
    escaped = False
    i = start + len(marker) - 1
    while i < len(source):
        ch = source[i]
        if in_string:
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


def js_obj_to_python(raw):
    """Convert JS object array (unquoted keys) to Python list."""
    jsonish = re.sub(r"([{,])([A-Za-z_][A-Za-z0-9_]*):", r'\1"\2":', raw)
    jsonish = re.sub(
        r"(?<=:|\[|,)\s*0x[0-9A-Fa-f]+",
        lambda m: str(int(m.group(0).strip(), 16)),
        jsonish,
    )
    return json.loads(f"[{jsonish}]")


def fetch_tables(source):
    mapping_match = re.search(r"const rl=\{(.*?)\},E1=", source, re.DOTALL)
    if not mapping_match:
        print("ERROR: mapping not found!")
        return {}

    tables = {}
    for m in re.finditer(r"([A-Za-z0-9_]+):([A-Za-z0-9_]+)", mapping_match.group(1)):
        slug = m.group(1).strip()
        var_name = m.group(2).strip()

        raw_array = extract_raw_array(source, var_name)
        if not raw_array:
            m2 = re.search(
                rf"\b{re.escape(var_name)}=JSON\.parse\('((?:\\.|[^'])*)'\)", source
            )
            if m2:
                raw = m2.group(1)
                try:
                    raw_array = json.loads(f'"{raw}"')
                except Exception:
                    pass

        if not raw_array:
            continue
        try:
            # extract_raw_array returns content with leading '[' but missing final ']'
            # Strip the '[' so js_obj_to_python can re-wrap correctly
            inner = raw_array.lstrip()
            if inner.startswith("["):
                inner = inner[1:]
            tables[slug] = js_obj_to_python(inner)
        except Exception as e:
            print(f"  PARSE ERROR for {slug} ({var_name}): {e}")
            continue

    return tables


def show_building(tables, slug, max_levels=8, world_pct=10, gov_pct=20):
    rows = tables.get(slug)
    if not rows:
        print(f"  [{slug}] NAO ENCONTRADO")
        return
    print(f"  Campos: {list(rows[0].keys())}")
    print(f"  {'Nivel':<6} {'Raw (ika-tools)':<16} {'Base s':<8} {'Base dur':<12} {'-10% mundo':<12} {'-20% gov':<12} {'Total reduc'}")
    print(f"  {'-'*90}")
    for row in rows[:max_levels]:
        lvl = row.get("level", "?")
        bt = row.get("building_time")
        base_s = _seconds_from_duration(str(bt or ""))
        after_world = _adjusted_time(base_s, world_pct, 0)
        after_both = _adjusted_time(base_s, world_pct, gov_pct)
        print(
            f"  {str(lvl):<6} {repr(str(bt)):<16} {base_s:<8} {_format_duration(base_s):<12} "
            f"{_format_duration(after_world):<12} {_format_duration(after_both):<12}"
        )


if __name__ == "__main__":
    print("Buscando bundle ika-tools...")
    home = requests.get(IKA_TOOLS_URL, timeout=20)
    m = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
    bundle_url = f"{IKA_TOOLS_URL}assets/{m.group(1)}"
    js = requests.get(bundle_url, timeout=30)
    source = js.text
    print(f"Bundle: {len(source):,} chars\n")

    tables = fetch_tables(source)
    print(f"Tabelas extraidas: {len(tables)}")
    print(f"Slugs: {sorted(tables.keys())}\n")

    WORLD = 10   # -10% mundo s78-br
    GOV = 20     # -20% governo

    for slug in ["governorsResidence", "townHall", "warehouse"]:
        print(f"=== {slug} (modificadores: -{WORLD}% mundo, -{GOV}% governo) ===")
        show_building(tables, slug, max_levels=8, world_pct=WORLD, gov_pct=GOV)
        print()
