#!/usr/bin/env python3
"""Validate component versions and require a bump when component code changes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMPONENTS = {
    "hub": ("hub_v2", ROOT / "hub_v2" / "VERSION"),
    "agent": ("agent_v2", ROOT / "agent_v2" / "VERSION"),
    "supervisor": ("supervisor_v1", ROOT / "supervisor_v1" / "VERSION"),
}


def parse_version(value: str, *, source: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"{source}: expected strict X.Y.Z, got {value!r}")
    return tuple(int(part) for part in match.groups())


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def version_at(ref: str, relative_path: str) -> str:
    return git("show", f"{ref}:{relative_path}")


def validate(base: str | None) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, (_, version_path) in COMPONENTS.items():
        value = version_path.read_text(encoding="utf-8").strip()
        parse_version(value, source=str(version_path.relative_to(ROOT)))
        versions[name] = value

    if not base:
        return versions

    changed = set(filter(None, git("diff", "--name-only", f"{base}...HEAD").splitlines()))
    errors: list[str] = []
    for name, (directory, version_path) in COMPONENTS.items():
        relative_version = version_path.relative_to(ROOT).as_posix()
        component_changed = any(
            path.startswith(f"{directory}/") and path != relative_version
            for path in changed
        )
        if not component_changed:
            continue
        if relative_version not in changed:
            errors.append(f"{name}: {relative_version} must change with {directory}/")
            continue
        try:
            old_text = version_at(base, relative_version).strip()
            old_version = parse_version(old_text, source=f"{base}:{relative_version}")
        except subprocess.CalledProcessError:
            continue
        if parse_version(versions[name], source=relative_version) <= old_version:
            errors.append(f"{name}: version must increase ({old_text} -> {versions[name]})")

    if errors:
        raise ValueError("\n".join(errors))
    return versions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="Git ref/SHA used to enforce component bumps")
    parser.add_argument("--github-output", help="Write component versions to this file")
    args = parser.parse_args()
    try:
        versions = validate(args.base)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"version guard failed:\n{exc}", file=sys.stderr)
        return 1

    for name, value in versions.items():
        print(f"{name}_version={value}")
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as output:
            for name, value in versions.items():
                output.write(f"{name}_version={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
