from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_add_login_block_fields_to_gameaccount"),
        ("proxy", "0002_change_webshare_id_to_charfield"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountProxyReservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="proxy_reservations", to="accounts.account")),
                ("proxy_profile", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="account_reservation", to="proxy.proxyprofile")),
            ],
            options={
                "ordering": ["created_at", "proxy_profile__address"],
            },
        ),
        migrations.AddConstraint(
            model_name="accountproxyreservation",
            constraint=models.UniqueConstraint(fields=("account", "proxy_profile"), name="uq_account_proxy_reservation_account_proxy"),
        ),
    ]
