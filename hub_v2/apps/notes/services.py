from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from apps.accounts.services.agent_versions import parse_version

from .models import ChangeLogEntry, Note, NoteEvent


DOCKER_HUB_API = "https://hub.docker.com/v2/repositories"
RELEASE_REPOSITORIES = {
    "hub": "blackoneal/ikabot-web-hub",
    "agent": "blackoneal/ikabot-web-agent",
}
RELEASE_CACHE_SECONDS = 300


@dataclass(frozen=True)
class RegistryRelease:
    component: str
    repository: str
    version: str = ""
    published_at: datetime | None = None
    digest: str = ""
    error: str = ""

    @property
    def image(self) -> str:
        if not self.version:
            return self.repository
        return f"{self.repository}:{self.version}"


def _release_cache_key(component: str) -> str:
    return f"ikabot:release-status:{component}"


def clear_release_cache() -> None:
    cache.delete_many(
        [_release_cache_key(component) for component in RELEASE_REPOSITORIES]
    )


def _release_from_payload(component: str, repository: str, payload: dict) -> RegistryRelease:
    candidates = []
    for item in payload.get("results") or []:
        version = str(item.get("name") or "").removeprefix("v")
        parsed = parse_version(version)
        if parsed is not None:
            candidates.append((parsed, version, item))

    if not candidates:
        return RegistryRelease(
            component=component,
            repository=repository,
            error="Nenhuma tag de versao publicada foi encontrada.",
        )

    _, version, item = max(candidates, key=lambda candidate: candidate[0])
    return RegistryRelease(
        component=component,
        repository=repository,
        version=version,
        published_at=parse_datetime(str(item.get("last_updated") or "")),
        digest=str(item.get("digest") or ""),
    )


def get_registry_release(component: str, *, force: bool = False) -> RegistryRelease:
    """Return the highest semantic version published for a component."""
    repository = RELEASE_REPOSITORIES[component]
    key = _release_cache_key(component)
    if not force:
        cached = cache.get(key)
        if isinstance(cached, RegistryRelease):
            return cached

    try:
        response = requests.get(
            f"{DOCKER_HUB_API}/{repository}/tags",
            params={"page_size": 100, "ordering": "last_updated"},
            timeout=5,
        )
        response.raise_for_status()
        release = _release_from_payload(component, repository, response.json())
    except (requests.RequestException, TypeError, ValueError) as exc:
        release = RegistryRelease(
            component=component,
            repository=repository,
            error=f"Docker Hub indisponivel: {exc}",
        )

    cache.set(key, release, RELEASE_CACHE_SECONDS)
    return release


def record_change(
    *,
    title: str,
    body: str = "",
    component: str = "hub",
    version: str = "",
    visibility: str = "dev",
    dev_version: str = "",
    published_version: str = "",
    note: Note | None = None,
    username: str = "",
) -> ChangeLogEntry:
    user = None
    if username:
        user = get_user_model().objects.filter(username=username).first()
    entry = ChangeLogEntry.objects.create(
        component=component,
        visibility=visibility,
        version=version,
        dev_version=dev_version,
        published_version=published_version,
        title=title,
        body=body,
        note=note,
        created_by=user,
    )
    if note is not None:
        NoteEvent.objects.create(
            note=note,
            event_type="changelog",
            message=f"Changelog registrado: {entry.title}",
            actor_label=username or "sistema",
            metadata={
                "changelog_id": str(entry.pk),
                "visibility": entry.visibility,
                "version": entry.version,
            },
        )
    return entry
