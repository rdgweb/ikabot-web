from django.db import migrations

NEW = {
    "combat_report": ("⚔️", "{headline} · {city}", "{versus}\n{losses}\n{loot_line}"),
    "proxy_change": ("🔁", "Proxies: verificação automática", "{changes}\n{provider}\n{tests}\n{reason_line}"),
}


def create(apps, schema_editor):
    Template = apps.get_model("telegram", "NotificationTemplate")
    for event_key, (icon, title, body) in NEW.items():
        Template.objects.get_or_create(
            scope_key=f"{event_key}:global",
            defaults={"event_key": event_key, "icon": icon, "title_template": title, "body_template": body},
        )


class Migration(migrations.Migration):
    """N-109: the combat report and the proxy change get a text of their own.

    Data only: creates the two global texts when they do not exist. No text that is already
    there is changed.
    """

    dependencies = [("telegram", "0016_n104_templates_and_digest")]

    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
