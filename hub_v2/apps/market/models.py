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
    target_city_id = models.IntegerField(null=True, blank=True)

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
    unit_price = models.IntegerField(default=0)
    price_min = models.IntegerField(default=0)
    price_max = models.IntegerField(default=0)
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

    @property
    def total_value(self) -> int:
        return int(self.amount or 0) * int(self.unit_price or 0)


class BlackMarketOffer(UUIDTimestampModel):
    """Tracks offers listed on the game's Black Market by this hub."""

    STATUS_CHOICES = [
        ("active", "Ativa"),
        ("sold", "Vendida"),
        ("cancelled", "Cancelada"),
        ("expired", "Expirada"),
    ]

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="black_market_offers",
    )
    city_id = models.IntegerField()
    city_name = models.CharField(max_length=128, blank=True, default="")
    unit_id = models.IntegerField()
    unit_name = models.CharField(max_length=128, blank=True, default="")
    amount = models.IntegerField()
    unit_price = models.IntegerField()
    offer_resource = models.IntegerField(default=5)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="active", db_index=True)
    game_offer_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    listed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["game_account", "status"]),
            models.Index(fields=["unit_id", "status"]),
            models.Index(fields=["game_account", "city_id", "unit_id"]),
        ]

    def __str__(self):
        return f"BM {self.id} [{self.status}] {self.amount}x unit{self.unit_id} @ {self.unit_price}"

    @classmethod
    def avg_price(cls, unit_id: int) -> float | None:
        """Return avg sell price for this unit across all historical offers."""
        from django.db.models import Avg
        result = cls.objects.filter(unit_id=unit_id).aggregate(avg=Avg("unit_price"))
        return result["avg"]


class BlackMarketUnitQuote(UUIDTimestampModel):
    """Snapshot of Black Market unit price ranges captured during a runner execution."""

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="black_market_unit_quotes",
    )
    city_id = models.IntegerField()
    city_name = models.CharField(max_length=128, blank=True, default="")
    unit_id = models.IntegerField()
    unit_name = models.CharField(max_length=128, blank=True, default="")
    offer_resource = models.IntegerField(default=5)
    price_min = models.IntegerField(default=0)
    price_max = models.IntegerField(default=0)
    available_amount = models.IntegerField(default=0)
    active_offer_amount = models.IntegerField(default=0)
    active_offer_price = models.IntegerField(default=0)
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    captured_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["game_account", "city_id", "offer_resource"]),
            models.Index(fields=["unit_id", "offer_resource"]),
        ]

    def __str__(self):
        return (
            f"BMQuote {self.id} ga={self.game_account_id} city={self.city_id} "
            f"unit={self.unit_id} res={self.offer_resource} "
            f"{self.price_min}-{self.price_max} avail={self.available_amount}"
        )


class BlackMarketAvailableOffer(UUIDTimestampModel):
    """Available unit offers seen at a buyer's Branch Office (scanned by runner 808).

    Cleared and replaced on each scan for the same game_account + buyer_city_id.
    """

    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="bm_available_offers",
    )
    buyer_city_id = models.IntegerField()
    seller_city_id = models.IntegerField()
    seller_city_name = models.CharField(max_length=128, blank=True, default="")
    seller_avatar = models.CharField(max_length=128, blank=True, default="")
    unit_id = models.IntegerField()
    unit_category = models.IntegerField(default=444)
    amount = models.IntegerField(default=0)
    price_per_unit = models.IntegerField(default=0)
    scanned_at = models.DateTimeField(null=True, blank=True)
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["price_per_unit"]
        indexes = [
            models.Index(fields=["game_account", "buyer_city_id"]),
            models.Index(fields=["unit_id", "unit_category"]),
        ]

    def __str__(self):
        return (
            f"BMAvailOffer unit={self.unit_id} amt={self.amount} "
            f"price={self.price_per_unit} from={self.seller_avatar}"
        )


class ConstructionMarketIntervention(UUIDTimestampModel):
    STATUS_CHOICES = [
        ("pending", "Pendente"),
        ("approved", "Aprovada"),
        ("rejected", "Recusada"),
        ("sell_queued", "Venda enfileirada"),
        ("expired", "Expirada"),
    ]

    RESOURCE_CHOICES = InternalMarketOrder.RESOURCE_CHOICES

    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="construction_market_interventions",
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="construction_market_interventions",
    )
    node = models.ForeignKey(
        "accounts.Node",
        on_delete=models.CASCADE,
        related_name="construction_market_interventions",
    )
    source_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="construction_market_interventions",
    )
    sell_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="pending", db_index=True)
    wait_reason = models.CharField(max_length=64, blank=True, default="")
    eta_seconds = models.IntegerField(default=0)

    city_id = models.IntegerField(null=True, blank=True)
    city_name = models.CharField(max_length=128, blank=True, default="")
    building_name = models.CharField(max_length=128, blank=True, default="")

    needed_resource_idx = models.IntegerField(choices=RESOURCE_CHOICES)
    needed_amount = models.IntegerField(default=0)
    available_gold = models.IntegerField(default=0)
    min_gold = models.IntegerField(default=0)
    estimated_buy_unit_min = models.IntegerField(default=0)
    estimated_buy_unit_avg = models.IntegerField(default=0)
    estimated_buy_cost_min = models.IntegerField(default=0)
    estimated_buy_cost_avg = models.IntegerField(default=0)

    sale_city_id = models.IntegerField(null=True, blank=True)
    sale_city_name = models.CharField(max_length=128, blank=True, default="")
    sale_branchoffice_pos = models.IntegerField(null=True, blank=True)
    sale_resource_idx = models.IntegerField(choices=RESOURCE_CHOICES)
    sale_amount = models.IntegerField(default=0)
    sale_price_min = models.IntegerField(default=0)
    sale_price_max = models.IntegerField(default=0)
    sale_price_target = models.IntegerField(default=0)
    estimated_sale_gold = models.IntegerField(default=0)
    can_fund_min = models.BooleanField(default=False)
    can_fund_avg = models.BooleanField(default=False)

    telegram_chat_id = models.CharField(max_length=64, blank=True, default="")
    telegram_message_id = models.CharField(max_length=64, blank=True, default="")
    decision_note = models.TextField(blank=True, default="")
    decided_by = models.CharField(max_length=128, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["game_account", "status"]),
            models.Index(fields=["source_job", "status"]),
        ]

    def __str__(self):
        return f"CMI {self.id} [{self.status}] ga={self.game_account_id} need={self.needed_resource_idx}"
