"""N-88: the action menus became subjects; rewrite the category stored on each workflow.

Only the `category` label changes. No job, run or schedule is touched. The map is frozen
here (workflow type -> menu) so the migration does not depend on the catalog of the day.
"""

from django.db import migrations

TYPE_TO_CATEGORY = {
    "abandon_colony": "construction",
    "activate_miracle": "temple",
    "activate_shrine": "temple",
    "activate_shrine_loop": "temple",
    "adjust_scientists": "research",
    "alert_attacks": "monitoring",
    "alert_wine": "monitoring",
    "arrival_monitor": "resources",
    "auto_routine": "account",
    "barbarians": "military",
    "black_market_buy": "market",
    "black_market_cancel": "market",
    "black_market_sell": "market",
    "buildings_sync": "construction",
    "buy_market": "market",
    "buy_research": "research",
    "buy_resources": "market",
    "buy_ships": "resources",
    "check_bm_offers": "market",
    "check_status": "account",
    "colonize": "construction",
    "combat_monitor": "monitoring",
    "consolidate": "resources",
    "construct_building": "construction",
    "construction": "construction",
    "construction_plan": "construction",
    "demolish_buildings": "construction",
    "diplomacy": "diplomacy",
    "diplomacy_check": "diplomacy",
    "diplomacy_send": "diplomacy",
    "discover_characters": "account",
    "distribute": "resources",
    "donate": "resources",
    "donate_loop": "resources",
    "generals_bank_buy": "military",
    "generals_bank_manage": "military",
    "generals_bank_producer_task": "military",
    "internal_market_order": "market",
    "island_monitor": "monitoring",
    "login_daily": "account",
    "market_scan": "market",
    "market_sell_to_request": "market",
    "military_movements": "military",
    "modify_production": "resources",
    "pirate": "military",
    "premium_resources": "resources",
    "raid_city": "military",
    "rename_city": "account",
    "reorder_buildings": "construction",
    "research": "research",
    "revolt": "military",
    "sell_market": "market",
    "sell_resources": "market",
    "send_resources": "resources",
    "spy": "military",
    "station_units": "military",
    "status": "account",
    "train_units": "military",
    "transport_route": "resources",
    "upgrade_units": "military",
    "vacation_mode": "account",
    "world_dump": "monitoring",
    "world_spy": "military",
}
# a type this map does not know keeps its menu, unless the menu itself is gone
LEGACY_CATEGORY = {"economy": "resources", "automation": "resources", "admin": "account"}


def new_category(workflow_type: str, category: str) -> str:
    return TYPE_TO_CATEGORY.get(workflow_type or "") or LEGACY_CATEGORY.get(category or "", category or "")


def forwards(apps, schema_editor):
    Workflow = apps.get_model("jobs", "Workflow")
    pairs = Workflow.objects.order_by().values_list("workflow_type", "category").distinct()
    for workflow_type, category in list(pairs):
        target = new_category(workflow_type, category)
        if target != category:
            Workflow.objects.filter(workflow_type=workflow_type, category=category).update(category=target)


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0014_workflow_list_indexes"),
    ]

    operations = [
        # the old menus mixed subjects, so there is no faithful way back: reversing keeps the labels
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
