#!/usr/bin/env python3
"""Prune stale Docker Hub tags to stop unbounded storage growth (N-79).

Every push to main used to create 3-4 *permanent* tags per component
(latest, X.Y.Z, vX.Y.Z, sha-XXXXXXX) — the metadata-action config in
docker-publish.yml has since been trimmed to only create `latest` and
`vX.Y.Z` going forward, but the historical tags already published (240+
on the hub repo alone, ~248 on agent, ~21 on supervisor) are still
sitting there. Nothing in this codebase resolves images by sha-tag or by
the bare (no "v" prefix) version tag — apps/notes/services.py's
get_registry_release() strips an optional leading "v" from whatever tag
name it finds, so it works with either form, and only "vX.Y.Z" is kept
going forward.

Safe by design:
- Defaults to a dry run (only prints what WOULD be deleted). Pass
  --execute to actually delete.
- Never touches `latest` or any tag that looks like a release version
  (optionally "v"-prefixed X.Y.Z). Only sha-<commit> tags and the
  redundant bare X.Y.Z duplicates (where the "v"-prefixed twin also
  exists) are deletion candidates.
- Aborts with a clear, non-zero-exit error (no partial deletes) if
  login fails or a response doesn't look like what's expected — it
  never guesses its way past an unexpected API response.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

import requests

HUB_API = "https://hub.docker.com/v2"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
REPOSITORIES = [
    "blackoneal/ikabot-web-hub",
    "blackoneal/ikabot-web-agent",
    "blackoneal/ikabot-web-supervisor",
]


def login(username: str, password: str) -> str:
    resp = requests.post(
        f"{HUB_API}/users/login/",
        json={"username": username, "password": password},
        timeout=15,
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Docker Hub login failed ({resp.status_code}): {resp.text[:300]}"
        )
    token = resp.json().get("token")
    if not token:
        raise SystemExit("Docker Hub login response had no token — aborting, not guessing.")
    return token


def list_tags(repository: str, token: str) -> list[dict]:
    tags: list[dict] = []
    url = f"{HUB_API}/repositories/{repository}/tags/"
    params = {"page_size": 100}
    headers = {"Authorization": f"JWT {token}"}
    while url:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise SystemExit(
                f"Failed to list tags for {repository} ({resp.status_code}): {resp.text[:300]}"
            )
        payload = resp.json()
        tags.extend(payload.get("results") or [])
        url = payload.get("next")
        params = None  # `next` already carries the query string
    return tags


def delete_tag(repository: str, tag: str, token: str) -> None:
    resp = requests.delete(
        f"{HUB_API}/repositories/{repository}/tags/{tag}/",
        headers={"Authorization": f"JWT {token}"},
        timeout=15,
    )
    if resp.status_code not in (200, 202, 204):
        raise SystemExit(
            f"Failed to delete {repository}:{tag} ({resp.status_code}): {resp.text[:300]}"
        )


def classify(tag_names: set[str]) -> tuple[set[str], set[str]]:
    """Return (keep, delete) tag names.

    Keep: "latest" and every "vX.Y.Z" version tag.
    Delete: "sha-*" tags, and bare "X.Y.Z" tags whose "vX.Y.Z" twin is
    also present (the exact-duplicate case) — a bare version tag with
    no "v"-prefixed twin is left alone, since we can't tell whether
    something external still depends on that exact name.
    """
    keep: set[str] = set()
    delete: set[str] = set()
    for name in tag_names:
        if name == "latest":
            keep.add(name)
        elif name.startswith("sha-"):
            delete.add(name)
        elif name.startswith("v") and VERSION_RE.fullmatch(name):
            keep.add(name)
        elif VERSION_RE.fullmatch(name):
            if f"v{name}" in tag_names:
                delete.add(name)
            else:
                keep.add(name)
        else:
            keep.add(name)  # unknown pattern — never delete something we don't recognise
    return keep, delete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry run)")
    parser.add_argument("--repository", action="append", dest="repositories", help="Limit to one repo (repeatable)")
    parser.add_argument("--sleep", type=float, default=0.3, help="Seconds between delete calls (rate-limit friendly)")
    args = parser.parse_args()

    username = os.environ.get("DOCKERHUB_USERNAME", "")
    password = os.environ.get("DOCKERHUB_TOKEN", "")
    if not username or not password:
        raise SystemExit("DOCKERHUB_USERNAME / DOCKERHUB_TOKEN must be set in the environment.")

    token = login(username, password)

    repos = args.repositories or REPOSITORIES
    grand_total_kept = 0
    grand_total_deleted = 0

    for repository in repos:
        tags = list_tags(repository, token)
        tag_names = {t["name"] for t in tags}
        keep, delete = classify(tag_names)

        print(f"\n=== {repository} ===")
        print(f"total tags: {len(tag_names)} | keep: {len(keep)} | delete: {len(delete)}")
        grand_total_kept += len(keep)
        grand_total_deleted += len(delete)

        for name in sorted(delete):
            if args.execute:
                delete_tag(repository, name, token)
                print(f"  deleted {name}")
                time.sleep(args.sleep)
            else:
                print(f"  would delete {name}")

    mode = "EXECUTED" if args.execute else "DRY RUN"
    print(f"\n[{mode}] total kept: {grand_total_kept} | total {'deleted' if args.execute else 'to delete'}: {grand_total_deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
