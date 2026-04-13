"""Inspect ika-tools bundle to find the data mapping pattern."""
import re
import requests

IKA_TOOLS_URL = "https://ika-tools.com/"

home = requests.get(IKA_TOOLS_URL, timeout=20)
match = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
bundle_url = f"{IKA_TOOLS_URL}assets/{match.group(1)}"
print(f"Bundle: {bundle_url}")

js = requests.get(bundle_url, timeout=30)
source = js.text
print(f"Bundle size: {len(source):,} chars\n")

# Try various patterns for the mapping
patterns = [
    r"const rl=\{",
    r"governorsResidence",
    r"palaceColony",
    r"townHall",
    r"building_time",
    r"buildingTime",
    r"warehouse",
]
for pat in patterns:
    m = re.search(pat, source)
    if m:
        start = max(0, m.start() - 50)
        end = min(len(source), m.end() + 200)
        print(f"FOUND '{pat}' at pos {m.start()}:")
        print(f"  ...{repr(source[start:end])}...")
        print()
    else:
        print(f"NOT FOUND: '{pat}'")
        print()
