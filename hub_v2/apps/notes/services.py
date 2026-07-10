from django.contrib.auth import get_user_model

from .models import ChangeLogEntry, Note


def record_change(
    *,
    title: str,
    body: str = "",
    component: str = "hub",
    version: str = "",
    note: Note | None = None,
    username: str = "",
) -> ChangeLogEntry:
    user = None
    if username:
        user = get_user_model().objects.filter(username=username).first()
    return ChangeLogEntry.objects.create(
        component=component,
        version=version,
        title=title,
        body=body,
        note=note,
        created_by=user,
    )
