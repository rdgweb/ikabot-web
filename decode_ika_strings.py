"""Decode ika-tools bundle string constants to understand the calculation formula."""
import re
import requests

IKA_TOOLS_URL = "https://ika-tools.com/"

home = requests.get(IKA_TOOLS_URL, timeout=20)
m = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
source = requests.get(f"{IKA_TOOLS_URL}assets/{m.group(1)}", timeout=30).text
print(f"Bundle size: {len(source):,} chars")

# Extract the string constants array from function xt(){const r=[...]}
m2 = re.search(r"function xt\(\)\{const r=\[", source)
start_pos = m2.start() + len("function xt(){const r=[")

# Find matching ]
depth = 1
i = start_pos
while i < len(source) and depth > 0:
    if source[i] == "[":
        depth += 1
    elif source[i] == "]":
        depth -= 1
    i += 1
array_text = source[start_pos : i - 1]

# Parse JS strings (both single and double quoted) handling escape sequences
strings = []
j = 0
while j < len(array_text):
    if array_text[j] in ('"', "'"):
        q = array_text[j]
        j += 1
        s = []
        while j < len(array_text):
            c = array_text[j]
            if c == "\\":
                j += 1
                if j < len(array_text):
                    s.append(array_text[j])
            elif c == q:
                break
            else:
                s.append(c)
            j += 1
        strings.append("".join(s))
    j += 1

print(f"Total strings: {len(strings)}")

# Find the offset from F(r) function: return xt()[r - OFFSET]
offset_m = re.search(r"xt\(\)\[r-(\d+)\]", source)
if not offset_m:
    print("ERROR: Could not find offset")
    exit(1)
offset = int(offset_m.group(1))
print(f"Offset: r - {offset}")


def S(r):
    idx = r - offset
    if 0 <= idx < len(strings):
        return strings[idx]
    return f"??{r}??"


# Decode the key variables in the formula
print()
print("=== Key string constants ===")
for r in range(173, 173 + len(strings)):
    val = S(r)
    if any(
        k in val
        for k in [
            "building_time",
            "selectBuilding",
            "Chronos",
            "chronos",
            "Building",
            "building",
            "factor",
            "world",
            "time",
            "speed",
            "reduction",
            "input",
            "Input",
            "bn",
        ]
    ):
        print(f"  S({r}) = {repr(val)}")

print()
print("=== Formula variable decode ===")
# Key indices found in the formula: 180, 181, 188, 190, 199, 200, 204, 206, 209, 243, 266, 285, 308, 309, 313
for r in [
    173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188,
    189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204,
    205, 206, 207, 208, 209, 210, 211, 233, 241, 243, 244, 245, 248, 250, 251, 252,
    253, 255, 261, 265, 266, 273, 278, 279, 280, 281, 282, 283, 284, 285, 288, 291,
    292, 293, 297, 299, 302, 305, 308, 309, 313, 315, 319, 320, 321, 330, 332, 336,
    337, 340, 341, 343, 344,
]:
    print(f"  S({r}) = {repr(S(r))}")
