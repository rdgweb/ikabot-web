"""Debug the rl mapping extraction."""
import re
import json
import requests

IKA_TOOLS_URL = "https://ika-tools.com/"

home = requests.get(IKA_TOOLS_URL, timeout=20)
match = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
bundle_url = f"{IKA_TOOLS_URL}assets/{match.group(1)}"
js = requests.get(bundle_url, timeout=30)
source = js.text

# Try original regex
mapping_match = re.search(r"const rl=\{(.*?)\},E1=", source, re.DOTALL)
if mapping_match:
    print("Found with original regex 'E1='")
    print(f"Matched content (first 300 chars): {repr(mapping_match.group(1)[:300])}")
else:
    print("NOT found with original regex 'E1='")
    # Find what comes after the rl object
    rl_start = source.find("const rl={")
    if rl_start >= 0:
        snippet = source[rl_start:rl_start+500]
        print(f"Context around 'const rl={{': {repr(snippet)}")
        # Find the closing of the object
        # Count braces
        depth = 0
        for i, ch in enumerate(source[rl_start:], start=rl_start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    print(f"\nObject ends at pos {end}")
                    print(f"After object: {repr(source[end:end+100])}")
                    break

# Try more flexible patterns
for pattern in [
    r"const rl=\{(.*?)\}[,;]",
    r"rl=\{(.*?)\}[,;]",
]:
    m = re.search(pattern, source, re.DOTALL)
    if m:
        content = m.group(1)
        # Extract slug:varname pairs
        pairs = re.findall(r"([A-Za-z0-9_]+):([A-Za-z0-9_]+)", content)
        print(f"\nPattern '{pattern}' found {len(pairs)} pairs")
        print(f"First 5: {pairs[:5]}")
        # Try to find governorsResidence
        for slug, var in pairs:
            if 'governor' in slug.lower() or 'palace' in slug.lower() or 'townHall' in slug:
                print(f"  -> {slug}: {var}")
                # Try to find the array for this var
                marker = f"{var}=["
                pos = source.find(marker)
                if pos >= 0:
                    print(f"     Array found at {pos}: {repr(source[pos:pos+100])}")
                else:
                    # Try JSON.parse pattern
                    m2 = re.search(rf"\b{re.escape(var)}=JSON\.parse\('", source)
                    if m2:
                        print(f"     JSON.parse found at {m2.start()}: {repr(source[m2.start():m2.start()+100])}")
                    else:
                        print(f"     NOT FOUND as array or JSON.parse for var '{var}'")
        break
