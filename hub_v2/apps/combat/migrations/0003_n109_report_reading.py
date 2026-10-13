from django.db import migrations, models


def backfill(apps, schema_editor):
    """The reports kept before N-109 are old news: read their names, mark them as already told."""
    from apps.combat.services.report_parser import parse_rounds, parse_summary

    Report = apps.get_model("combat", "CombatReport")
    for report in Report.objects.all():
        try:
            rounds, summary = parse_rounds(report.detailed_html), parse_summary(report.summary_html)
        except Exception:  # noqa: BLE001 - a page that cannot be read keeps its row as it is
            rounds, summary = [], None
        last = rounds[-1] if rounds else {}
        report.battle_title = (last.get("title") or (summary or {}).get("title") or "")[:160]
        report.attacker_name = ((summary or {}).get("attacker", {}).get("name") or last.get("attacker", {}).get("name") or "")[:160]
        report.defender_name = ((summary or {}).get("defender", {}).get("name") or last.get("defender", {}).get("name") or "")[:160]
        report.finished = True
        report.notified_final = True
        report.notified_round = max(report.total_rounds or 0, last.get("round") or 0)
        report.save(update_fields=["battle_title", "attacker_name", "defender_name", "finished", "notified_final", "notified_round"])


class Migration(migrations.Migration):
    """N-109: what the hub reads from a combat report, and what it already told about it.

    Adds six columns to combat_combatreport. The data step fills them for the reports that
    already exist and marks those as told, so that none of them is announced again.
    """

    dependencies = [("combat", "0002_auto_rename_indexes")]

    operations = [
        migrations.AddField(model_name="combatreport", name="battle_title", field=models.CharField(blank=True, default="", max_length=160)),
        migrations.AddField(model_name="combatreport", name="attacker_name", field=models.CharField(blank=True, default="", max_length=160)),
        migrations.AddField(model_name="combatreport", name="defender_name", field=models.CharField(blank=True, default="", max_length=160)),
        migrations.AddField(model_name="combatreport", name="finished", field=models.BooleanField(db_index=True, default=False)),
        migrations.AddField(model_name="combatreport", name="notified_round", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="combatreport", name="notified_final", field=models.BooleanField(default=False)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
