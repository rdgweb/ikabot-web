"""
Model: InternalMarketOrder — resource trading between accounts.
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class InternalMarketOrder(UUIDTimestampModel):
    """
    An internal resource trade order between player accounts.
    Managed by the market engine service.
    """

    STATUS_CHOICES = [
        ("created", "Criado"),
        ("matched", "Pareado"),
        ("jobs_created", "Jobs criados"),
        ("jobs_running", "Jobs executando"),
        ("completed", "Concluído"),
        ("failed", "Falhou"),
        ("canceled", "Cancelado"),
    ]

    RESOURCE_CHOICES = [
        (0, "Madeira"),
        (1, "Vinho"),
        (2, "Mármore"),
        (3, "Cristal"),
        (4, "Enxofre"),
    ]

    # Buyer
    buyer_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="market_orders_as_buyer",
    )
    buyer_game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_orders_as_buyer_ga",
    )
    buyer_node = models.ForeignKey(
        "accounts.Node",
        on_delete=models.CASCADE,
        related_name="market_orders_buyer_node",
    )
    buyer_city_index = models.IntegerField(default=1)
    buyer_market_city_index = models.IntegerField(null=True, blank=True)
    # Resolved at matching time — actual game city ID and Branch Office slot
    buyer_city_id = models.IntegerField(null=True, blank=True)
    buyer_branchoffice_pos = models.IntegerField(null=True, blank=True)

    # Seller (matched later)
    seller_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_orders_as_seller",
    )
    seller_game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_orders_as_seller_ga",
    )
    seller_node = models.ForeignKey(
        "accounts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_orders_seller_node",
    )
    seller_city_index = models.IntegerField(null=True, blank=True)
    # Resolved at matching time — actual game city ID and Branch Office slot
    seller_city_id = models.IntegerField(null=True, blank=True)
    seller_branchoffice_pos = models.IntegerField(null=True, blank=True)

    # Order details
    resource_idx = models.IntegerField(choices=RESOURCE_CHOICES)
    amount = models.IntegerField(default=0)
    unit_price = models.IntegerField(default=12)
    status = models.CharField(max_length=32, default="created", choices=STATUS_CHOICES)

    # Related jobs
    buy_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sell_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    redistribution_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # Source context
    source_action_code = models.IntegerField(null=True, blank=True)
    source_reason = models.CharField(max_length=128, blank=True, default="")
    reason_detail = models.TextField(blank=True, default="")
    production_eta_seconds = models.IntegerField(null=True, blank=True)
    missing_resource_keys = models.CharField(max_length=128, blank=True, default="")
    result_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.id} [{self.status}] {self.amount}x res{self.resource_idx}"
