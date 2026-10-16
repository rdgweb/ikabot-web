"""
Models: Profile (legacy) + Preset (F006) — reusable multi-action × multi-account templates.
"""

from django.conf import settings
from django.db import models

from apps.orgs.scope import OrgScopedManager
from apps.orgs.services import default_organization_id

from core.mixins.models import UUIDTimestampModel


class Profile(UUIDTimestampModel):
    """
    Legacy: A reusable action template that can be applied to accounts.
    Stores action_code + default inputs for quick job creation.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    name = models.CharField(max_length=96)   # N-106: unique per organization (Meta)
    description = models.TextField(blank=True, default="")
    # N-106: who owns it. Stage 1 fills it with the default organization; nothing filters by it yet.
    organization = models.ForeignKey(
        "orgs.Organization", on_delete=models.PROTECT, null=True, blank=True,
        default=default_organization_id, related_name="profiles",
    )
    action_code = models.IntegerField()
    inputs_json = models.TextField(
        default="[]", help_text="JSON array of default input values"
    )
    timeout_sec = models.IntegerField(default=1800)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uq_profile_org_name")]

    def __str__(self):
        return self.name


# ── Preset (F006) ─────────────────────────────────────────────────────────────


class Preset(UUIDTimestampModel):
    """
    A saved multi-action × multi-account template.
    When executed it creates one job per (game_account, preset_action) pair.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    name = models.CharField(max_length=128)   # N-106: unique per organization (Meta)
    description = models.TextField(blank=True, default="")
    # N-142: the Ações do Jogo category it shows up in, ready to run ("" = only in Presets)
    catalog_category = models.CharField(max_length=32, blank=True, default="")
    # N-106: who owns it. Stage 1 fills it with the default organization; nothing filters by it yet.
    organization = models.ForeignKey(
        "orgs.Organization", on_delete=models.PROTECT, null=True, blank=True,
        default=default_organization_id, related_name="presets",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="presets",
    )

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uq_preset_org_name")]

    def __str__(self):
        return self.name

    @property
    def account_count(self):
        return self.preset_accounts.count()

    @property
    def action_count(self):
        return self.actions.count()

    @property
    def job_count(self):
        return self.account_count * self.action_count


class PresetGameAccount(models.Model):
    """Through model linking a Preset to selected GameAccounts."""
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    preset = models.ForeignKey(
        Preset, on_delete=models.CASCADE, related_name="preset_accounts"
    )
    game_account = models.ForeignKey(
        "accounts.GameAccount", on_delete=models.CASCADE, related_name="preset_links"
    )

    class Meta:
        unique_together = [["preset", "game_account"]]
        ordering = ["game_account__account__label", "game_account__server_id"]

    def __str__(self):
        return f"{self.preset} × {self.game_account}"


class PresetAction(UUIDTimestampModel):
    """
    One action slot inside a Preset.
    Stores default inputs (all accounts) and optional per-account overrides.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    preset = models.ForeignKey(
        Preset, on_delete=models.CASCADE, related_name="actions"
    )
    action_code = models.IntegerField()
    action_name = models.CharField(max_length=128, default="")
    order = models.PositiveIntegerField(default=0)
    # Default config applied to all accounts
    inputs_json = models.TextField(default="{}")
    # Per-account overrides: {str(ga_pk): {inputs}}
    per_account_json = models.TextField(default="{}")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.preset} / ac={self.action_code} (order={self.order})"
