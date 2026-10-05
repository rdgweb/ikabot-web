"""Mercado geral: gravar e ler as varreduras do runner 810 (N-84)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.game.models import AccountSnapshot

from .models import PublicMarketOffer, PublicMarketScan

MAX_OFFERS_PER_SCAN = 2000
_KIND_BY_TYPE = {444: "sell", 333: "buy"}


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def managed_city_ids() -> set[int]:
    """Ids of every city of the accounts this hub manages."""
    ids: set[int] = set()
    for cities in AccountSnapshot.objects.values_list("cities", flat=True):
        for city in cities if isinstance(cities, list) else []:
            if isinstance(city, dict) and city.get("id") is not None:
                ids.add(_int(city.get("id")))
    ids.discard(0)
    return ids


def save_public_market_scans(game_account, scans: list, *, job=None) -> dict[str, int]:
    """Replace what each scanned market city of this account sees. Returns counters."""
    now = timezone.now()
    internal = managed_city_ids()
    created = saved_scans = 0
    with transaction.atomic():
        for entry in scans if isinstance(scans, list) else []:
            if not isinstance(entry, dict) or _int(entry.get("city_id")) <= 0:
                continue
            kind = "range_sync" if entry.get("kind") == "range_sync" else "full"
            city_id = _int(entry.get("city_id"))
            existing = PublicMarketScan.objects.filter(game_account=game_account, city_id=city_id).first()
            if kind == "range_sync" and existing is not None and existing.kind == "full":
                # keep the offers of the last full scan; only the range information is new
                existing.bo_level = _int(entry.get("bo_level"))
                existing.max_range = _int(entry.get("max_range"))
                existing.range_before = _int(entry.get("range_before"))
                existing.city_name = str(entry.get("city_name") or existing.city_name)[:128]
                existing.save(update_fields=["bo_level", "max_range", "range_before", "city_name", "updated_at"])
                saved_scans += 1
                continue
            if existing is not None:
                existing.delete()
            offers = [o for o in (entry.get("offers") or []) if isinstance(o, dict)][:MAX_OFFERS_PER_SCAN]
            scan = PublicMarketScan.objects.create(
                game_account=game_account,
                city_id=city_id,
                city_name=str(entry.get("city_name") or city_id)[:128],
                kind=kind,
                bo_level=_int(entry.get("bo_level")),
                max_range=_int(entry.get("max_range")),
                range_before=_int(entry.get("range_before")),
                offers_count=len(offers),
                scanned_at=now,
                job=job,
            )
            PublicMarketOffer.objects.bulk_create([
                PublicMarketOffer(
                    scan=scan,
                    kind=_KIND_BY_TYPE.get(_int(o.get("offer_type"), 444), "sell"),
                    resource_idx=min(4, max(0, _int(o.get("resource_idx")))),
                    city_id=_int(o.get("city_id")),
                    city_name=str(o.get("city_name") or "")[:128],
                    player_name=str(o.get("player_name") or "")[:128],
                    amount=max(0, _int(o.get("amount"))),
                    unit_price=max(0, _int(o.get("unit_price"))),
                    distance=max(0, _int(o.get("distance"))),
                    goods_per_minute=max(0, _int(o.get("goods_per_minute"))),
                    is_internal=_int(o.get("city_id")) in internal,
                )
                for o in offers
            ])
            created += len(offers)
            saved_scans += 1
    return {"scans": saved_scans, "created": created}


def public_market_overview(*, resource_idx=None, kind="sell", hide_internal=False, game_account_id=None) -> dict:
    """Offers of the latest scans, without repeating an offer seen from two cities."""
    scans = PublicMarketScan.objects.select_related("game_account").order_by("game_account__name", "city_name")
    offers = PublicMarketOffer.objects.select_related("scan", "scan__game_account").filter(kind=kind)
    if game_account_id:
        offers = offers.filter(scan__game_account_id=game_account_id)
    if resource_idx is not None:
        offers = offers.filter(resource_idx=resource_idx)
    if hide_internal:
        offers = offers.filter(is_internal=False)

    # the same offer shows up in every scan that reaches it: keep the most recent sighting
    best: dict[tuple, PublicMarketOffer] = {}
    for offer in offers.order_by("resource_idx", "unit_price", "distance"):
        key = (offer.kind, offer.resource_idx, offer.city_id)
        seen = best.get(key)
        if seen is None or offer.scan.scanned_at > seen.scan.scanned_at:
            best[key] = offer
    rows = sorted(best.values(), key=lambda o: (o.resource_idx, o.unit_price if kind == "sell" else -o.unit_price, o.distance))

    summary = []
    for idx, label in PublicMarketOffer.RESOURCE_CHOICES:
        all_rows = [o for o in best.values() if o.resource_idx == idx]
        external = [o for o in all_rows if not o.is_internal]
        prices = [o.unit_price for o in external if o.unit_price > 0]
        summary.append({
            "idx": idx,
            "label": label,
            "count": len(all_rows),
            "external_count": len(external),
            "external_amount": sum(o.amount for o in external),
            "best_price": (min(prices) if kind == "sell" else max(prices)) if prices else None,
        })
    return {"scans": list(scans), "offers": rows, "summary": summary}
