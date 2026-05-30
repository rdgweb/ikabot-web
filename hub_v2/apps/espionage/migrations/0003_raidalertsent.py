# Generated manually — only RaidAlertSent model (RenameIndex ops skipped due to DB state mismatch)

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_add_chronos_level_to_gameaccount'),
        ('espionage', '0002_spyreport_target_owner_id_expires_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='RaidAlertSent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('target_city_id', models.CharField(db_index=True, max_length=32)),
                ('last_report_id', models.CharField(blank=True, default='', max_length=32)),
                ('ignored_report_id', models.CharField(blank=True, default='', max_length=32)),
                ('last_alerted_at', models.DateTimeField(blank=True, null=True)),
                ('game_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='raid_alerts_sent', to='accounts.gameaccount')),
            ],
            options={
                'verbose_name': 'Raid Alert Sent',
            },
        ),
        migrations.AddConstraint(
            model_name='raidalertsent',
            constraint=models.UniqueConstraint(fields=('game_account', 'target_city_id'), name='uq_raidalert_ga_city'),
        ),
    ]
