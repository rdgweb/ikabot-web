"""
Generals Bank — orchestrated military unit accumulation and liquidation.

Flow:
  Accumulation: producers train units → list on Black Market → bank wakes, buys → sleeps
  Liquidation:  bank wakes → lists own units for internal buyers → buyers purchase → bank sleeps
"""

from django.db import models

from core.mixins.models import UUIDTimestampModel


class GeneralsBankConfig(UUIDTimestampModel):
    """One bank account configuration. Multiple banks allowed per lobby."""

    bank_game_account = models.OneToOneField(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="generals_bank_config",
    )
    buyer_city_id = models.IntegerField(
        null=True, blank=True,
        help_text="City with Branch Office used for purchasing from producers.",
    )
    is_active = models.BooleanField(default=True)
    auto_vacation = models.BooleanField(
        default=True,
        help_text="Automatically enter/exit vacation mode during cycles.",
    )
    auto_cycle_enabled = models.BooleanField(
        default=False,
        help_text="Keep a background manager loop active for this bank.",
    )
    auto_cycle_interval_minutes = models.IntegerField(
        default=30,
        help_text="When auto-cycle is enabled, wait this many minutes between cycle checks.",
    )
    min_gold_floor = models.IntegerField(
        default=10000,
        help_text="When gold drops below this, trigger liquidation cycle.",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Banco de Generais"
        verbose_name_plural = "Bancos de Generais"

    def __str__(self):
        return f"Banco: {self.bank_game_account.name}"


class GeneralsBankProducer(UUIDTimestampModel):
    """A producer account that trains and sells units to the bank."""

    bank_config = models.ForeignKey(
        GeneralsBankConfig,
        on_delete=models.CASCADE,
        related_name="producers",
    )
    producer_game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="generals_bank_producer_roles",
    )
    min_resource_reserves = models.JSONField(
        default=dict,
        help_text='Minimum resource stocks that cannot be consumed. e.g. {"wood": 500, "marble": 200}',
    )
    min_population_reserve = models.IntegerField(
        default=0,
        help_text="Population units that must remain free (not used for training).",
    )
    production_template = models.JSONField(
        default=dict,
        help_text='Units this producer should train per cycle. e.g. {"303": 10, "210": 5}',
    )
    sell_only_cycle_production = models.BooleanField(
        default=True,
        help_text="If enabled, the bank cycle only sells units produced above the producer's starting stock in that cycle.",
    )
    keep_net_gold_positive = models.BooleanField(
        default=True,
        help_text="Reduce or block training if projected military upkeep would make the producer's net gold/hour negative.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = [["bank_config", "producer_game_account"]]
        verbose_name = "Produtora"
        verbose_name_plural = "Produtoras"

    def __str__(self):
        return f"Produtora: {self.producer_game_account.name} → {self.bank_config.bank_game_account.name}"


class GeneralsBankCycle(UUIDTimestampModel):
    """A single accumulation or liquidation cycle."""

    MODE_CHOICES = [
        ("accumulation", "Acumulação"),
        ("liquidation", "Liquidação"),
    ]
    STATUS_CHOICES = [
        ("training", "Treinando"),
        ("consolidating", "Consolidando"),
        ("listing", "Listando no BM"),
        ("bank_buying", "Banco comprando"),
        ("bank_listing", "Banco listando para venda"),
        ("buyers_buying", "Compradores comprando"),
        ("sleeping", "Entrando em férias"),
        ("completed", "Concluído"),
        ("failed", "Falhou"),
        ("cancelled", "Cancelado"),
    ]

    bank_config = models.ForeignKey(
        GeneralsBankConfig,
        on_delete=models.CASCADE,
        related_name="cycles",
    )
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="accumulation")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="training", db_index=True)
    target_units = models.JSONField(
        default=dict,
        help_text='Target units to produce. e.g. {"211": 50, "315": 100}',
    )
    estimated_ready_at = models.DateTimeField(null=True, blank=True)
    manager_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )
    buy_job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
        help_text="Bank wake+buy job (ac=807)",
    )
    notes = models.TextField(blank=True, default="")
    result_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["bank_config", "status"]),
        ]
        verbose_name = "Ciclo"
        verbose_name_plural = "Ciclos"

    def __str__(self):
        return f"Ciclo {self.mode} [{self.status}] — {self.bank_config.bank_game_account.name}"

    @property
    def is_terminal(self):
        return self.status in {"completed", "failed", "cancelled"}

    @property
    def active_tasks(self):
        return self.tasks.exclude(status__in=["sold", "failed", "cancelled"])


class GeneralsBankCycleTask(UUIDTimestampModel):
    """Per-producer training + transport + listing task within a cycle."""

    STATUS_CHOICES = [
        ("training", "Treinando"),
        ("transporting", "Transportando"),
        ("listed", "Listado no BM"),
        ("sold", "Vendido"),
        ("failed", "Falhou"),
        ("cancelled", "Cancelado"),
    ]

    cycle = models.ForeignKey(
        GeneralsBankCycle,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    producer_game_account = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.CASCADE,
        related_name="generals_bank_tasks",
    )
    city_id = models.IntegerField(help_text="City where training happens.")
    city_name = models.CharField(max_length=128, blank=True, default="")
    bm_city_id = models.IntegerField(
        null=True, blank=True,
        help_text="City with Black Market where units are ultimately listed.",
    )
    bm_city_name = models.CharField(max_length=128, blank=True, default="")
    unit_id = models.IntegerField()
    unit_name = models.CharField(max_length=128, blank=True, default="")
    quantity_target = models.IntegerField(default=0)
    quantity_done = models.IntegerField(default=0)
    unit_price = models.IntegerField(default=0, help_text="Listed price (min price from game).")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="training", db_index=True)

    training_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    transport_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    sell_job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["cycle", "status"]),
        ]
        verbose_name = "Tarefa do Ciclo"
        verbose_name_plural = "Tarefas do Ciclo"

    def __str__(self):
        return (
            f"Task [{self.status}] {self.producer_game_account.name} "
            f"unit={self.unit_id} qty={self.quantity_target}"
        )


class GeneralsBankTransaction(UUIDTimestampModel):
    """Record of a buy or sell transaction within a bank cycle."""

    DIRECTION_CHOICES = [
        ("buy", "Compra"),
        ("sell", "Venda"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pendente"),
        ("completed", "Concluído"),
        ("failed", "Falhou"),
    ]

    cycle = models.ForeignKey(
        GeneralsBankCycle,
        on_delete=models.CASCADE,
        related_name="transactions",
        null=True, blank=True,
    )
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)
    unit_id = models.IntegerField()
    unit_name = models.CharField(max_length=128, blank=True, default="")
    quantity = models.IntegerField()
    unit_price = models.IntegerField()
    gold_delta = models.IntegerField(
        default=0,
        help_text="Positive = gold received. Negative = gold spent.",
    )
    counterpart_ga = models.ForeignKey(
        "accounts.GameAccount",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="generals_bank_transactions",
        help_text="The producer (on buy) or buyer account (on sell).",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["cycle", "direction"]),
        ]
        verbose_name = "Transação"
        verbose_name_plural = "Transações"

    def __str__(self):
        return (
            f"Transação {self.direction} unit={self.unit_id} "
            f"qty={self.quantity} price={self.unit_price} [{self.status}]"
        )
