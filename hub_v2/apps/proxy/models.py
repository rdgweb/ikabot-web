"""
Models: ProxyProfile — proxy management via Webshare.io or manual entry.
"""

from django.db import models

from core.mixins.models import TimestampModel


class ProxyProfile(TimestampModel):
    """
    A proxy entry synced from Webshare.io or manually configured.
    """

    class Provider(models.TextChoices):
        WEBSHARE = "webshare", "Webshare"
        MANUAL = "manual", "Manual"
        OTHER = "other", "Outro"

    webshare_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    provider = models.CharField(
        max_length=16,
        choices=Provider.choices,
        default=Provider.MANUAL,
        help_text="Source of this proxy entry",
    )
    address = models.CharField(max_length=255)
    port = models.IntegerField()
    username = models.CharField(max_length=128, blank=True, default="")
    password_enc = models.TextField(blank=True, default="")
    country_code = models.CharField(max_length=8, blank=True, default="")
    active = models.BooleanField(default=True)

    # Which node is using this proxy
    assigned_node = models.OneToOneField(
        "accounts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proxy_profile",
    )

    # Testing / health check
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.BooleanField(
        null=True, blank=True,
        help_text="True if last test succeeded, False if failed, null if never tested",
    )
    last_test_ip = models.CharField(
        max_length=64, blank=True, default="",
        help_text="External IP returned during last test",
    )
    last_test_latency_ms = models.IntegerField(
        null=True, blank=True,
        help_text="Latency in milliseconds from last test",
    )

    class Meta:
        ordering = ["address"]

    def __str__(self):
        return f"{self.address}:{self.port} ({self.country_code})"

    @property
    def proxy_url(self) -> str:
        """Format as http://user:pass@host:port"""
        if self.username:
            from core.encryption import decrypt

            try:
                password = decrypt(self.password_enc) if self.password_enc else ""
            except Exception:
                password = ""
            return f"http://{self.username}:{password}@{self.address}:{self.port}"
        return f"http://{self.address}:{self.port}"

    @property
    def test_status_label(self) -> str:
        """Human-readable test status."""
        if self.last_test_status is None:
            return "Nunca testado"
        return "OK" if self.last_test_status else "Falha"


class AccountProxyReservation(TimestampModel):
    """Stable proxy reservation for one lobby account."""

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="proxy_reservations",
    )
    proxy_profile = models.OneToOneField(
        ProxyProfile,
        on_delete=models.CASCADE,
        related_name="account_reservation",
    )

    class Meta:
        ordering = ["created_at", "proxy_profile__address"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "proxy_profile"],
                name="uq_account_proxy_reservation_account_proxy",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_id} -> {self.proxy_profile.address}:{self.proxy_profile.port}"
