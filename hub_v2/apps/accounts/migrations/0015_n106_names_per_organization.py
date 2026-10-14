from django.db import migrations, models


class Migration(migrations.Migration):
    """N-106 stage 3a: node and machine names are unique per organization, not in the whole platform."""

    dependencies = [
        ("accounts", "0014_n106_organization"),
    ]

    operations = [
        migrations.AlterField(model_name="node", name="name", field=models.CharField(max_length=64)),
        migrations.AlterField(model_name="dockerhost", name="name", field=models.CharField(max_length=80)),
        migrations.AddConstraint(
            model_name="node",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="uq_node_org_name"),
        ),
        migrations.AddConstraint(
            model_name="dockerhost",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="uq_dockerhost_org_name"),
        ),
    ]
