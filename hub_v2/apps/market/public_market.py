"""Mercado geral: gravar e ler as varreduras do runner 810 (N-84) e negociar a partir delas (N-87)."""

from __future__ import annotations

from urllib.parse import urlencode

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .models import PublicMarketOffer, PublicMarketScan

MAX_OFFERS_PER_SCAN = 2000
SCAN_ACTION_CODE = 810
BUY_ACTION_CODE = 8
SELL_ACTION_CODE = 811
ACTIVE_JOB_STATUSES = ("queued", "scheduled", "running")
_KIND_BY_TYPE = {444: "sell", 333: "buy"}
_INT_MAX = 2_147_483_647


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _amount(value) -> int:
    """A non-negative number that fits the column, whatever the scan sent."""
    return min(_INT_MAX, max(0, _int(value)))


def _city_ids(cities) -> set[int]:
    ids = {_int(city.get("id")) for city in (cities if isinstance(cities, list) else []) if isinstance(city, dict)}
    ids.discard(0)
    return ids


def _has_market(cities) -> bool:
    for city in cities if isinstance(cities, list) else []:
        for building in (city.get("buildings") or []) if isinstance(city, dict) else []:
            if isinstance(building, dict) and building.get("building") == "branchOffice":
                return True
    return False


def city_ids_by_account() -> dict:
    """{game_account_id: ids of its cities} for every account this hub manages."""
    return {
        ga_id: _city_ids(cities)
        for ga_id, cities in AccountSnapshot.objects.exclude(game_account=None).values_list("game_account_id", "cities")
    }


def managed_city_ids() -> set[int]:
    """Ids of every city of the accounts this hub manages."""
    ids: set[int] = set()
    for cities in AccountSnapshot.objects.values_list("cities", flat=True):
        ids |= _city_ids(cities)
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
                existing.bo_level = _amount(entry.get("bo_level"))
                existing.max_range = _amount(entry.get("max_range"))
                existing.range_before = _amount(entry.get("range_before"))
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
                bo_level=_amount(entry.get("bo_level")),
                max_range=_amount(entry.get("max_range")),
                range_before=_amount(entry.get("range_before")),
                offers_count=len(offers),
                scanned_at=now,
                job=job,
            )
            PublicMarketOffer.objects.bulk_create([
                PublicMarketOffer(
                    scan=scan,
                    kind=_KIND_BY_TYPE.get(_int(o.get("offer_type"), 444), "sell"),
                    resource_idx=min(4, max(0, _int(o.get("resource_idx")))),
                    city_id=_amount(o.get("city_id")),
                    city_name=str(o.get("city_name") or "")[:128],
                    player_name=str(o.get("player_name") or "")[:128],
                    amount=_amount(o.get("amount")),
                    unit_price=_amount(o.get("unit_price")),
                    distance=_amount(o.get("distance")),
                    goods_per_minute=_amount(o.get("goods_per_minute")),
                    is_internal=_int(o.get("city_id")) in internal,
                )
                for o in offers
            ])
            created += len(offers)
            saved_scans += 1
    return {"scans": saved_scans, "created": created}


def pending_scan_jobs() -> int:
    """Scans (action 810) still waiting or running."""
    return Job.objects.filter(action_code=SCAN_ACTION_CODE, status__in=ACTIVE_JOB_STATUSES).count()


def request_public_market_refresh(*, created_by=None, game_account_ids=None) -> dict[str, int]:
    """One scan job per active account with a market, over all its market cities.

    Skips accounts on vacation (the job would only wait) and accounts that already
    have a scan waiting or running. Returns {"created", "busy", "vacation", "no_market"}.
    """
    from apps.game.services.vacation import read_vacation_state
    from apps.jobs.services.workflows import create_job_with_workflow

    counters = {"created": 0, "busy": 0, "vacation": 0, "no_market": 0}
    accounts = GameAccount.objects.filter(active=True, account__active=True).select_related("account", "account__node")
    if game_account_ids is not None:
        accounts = accounts.filter(pk__in=list(game_account_ids))
    accounts = list(accounts)
    snapshots = {snap.game_account_id: snap for snap in AccountSnapshot.objects.filter(game_account__in=accounts)}
    busy = set(
        Job.objects.filter(action_code=SCAN_ACTION_CODE, status__in=ACTIVE_JOB_STATUSES, game_account__in=accounts)
        .values_list("game_account_id", flat=True)
    )
    for ga in accounts:
        snap = snapshots.get(ga.pk)
        if snap is None or not _has_market(snap.cities) or ga.account.node is None:
            counters["no_market"] += 1
            continue
        if read_vacation_state(snap.base_snapshot).get("active"):
            counters["vacation"] += 1
            continue
        if ga.pk in busy:
            counters["busy"] += 1
            continue
        create_job_with_workflow(
            account=ga.account, game_account=ga, node=ga.account.node, action_code=SCAN_ACTION_CODE,
            inputs={"all_cities": True, "include_buy_requests": True, "max_pages": 5, "requested_by": "mercado_geral_atualizar"},
            status="queued", created_by=created_by,
        )
        counters["created"] += 1
    return counters


def _trade_url(offer: PublicMarketOffer, sighting: PublicMarketOffer) -> str:
    """Job form of the account that sees the offer, already filled in.

    A sell offer opens "Comprar do Mercado"; a buy request opens "Vender para Pedido de
    Compra". The scanned price goes as the price limit, so a changed offer is refused.
    """
    label = f"{offer.city_name} ({offer.player_name})" if offer.player_name else offer.city_name
    scan = sighting.scan
    if offer.kind == "sell":
        params = {
            "ga": scan.game_account_id, "action": BUY_ACTION_CODE,
            "input_buyer_city_id": scan.city_id, "input_seller_city_id": offer.city_id,
            "input_resource_idx": offer.resource_idx, "input_amount": offer.amount,
            "input_max_unit_price": offer.unit_price, "input_seller_label": label,
        }
    else:
        params = {
            "ga": scan.game_account_id, "action": SELL_ACTION_CODE,
            "input_city_id": scan.city_id, "input_buyer_city_id": offer.city_id,
            "input_resource_idx": offer.resource_idx, "input_amount": offer.amount,
            "input_min_unit_price": offer.unit_price, "input_buyer_label": label,
        }
    return f"{reverse('jobs:job-form')}?{urlencode(params)}"


def public_market_overview(*, resource_idx=None, kind="sell", hide_internal=False, game_account_id=None) -> dict:
    """Offers of the latest scans, each one once however many markets see it.

    Every row carries `trades`: the accounts that reach it (nearest first), each with the
    link that opens the buy/sell action already filled in.
    """
    scans = PublicMarketScan.objects.select_related("game_account").order_by("game_account__name", "city_name")
    offers = PublicMarketOffer.objects.select_related("scan", "scan__game_account").filter(kind=kind)
    if game_account_id:
        offers = offers.filter(scan__game_account_id=game_account_id)
    if hide_internal:
        offers = offers.filter(is_internal=False)

    # the same offer shows up in every scan that reaches it
    groups: dict[tuple, list[PublicMarketOffer]] = {}
    for offer in offers:
        groups.setdefault((offer.kind, offer.resource_idx, offer.city_id), []).append(offer)

    own_cities = city_ids_by_account()
    rows: list[PublicMarketOffer] = []
    for sightings in groups.values():
        sightings.sort(key=lambda o: (o.distance, -o.scan.scanned_at.timestamp()))
        latest = max(sightings, key=lambda o: o.scan.scanned_at)
        row = sightings[0]
        # numbers as last seen; the nearest market is the one offered first to trade
        row.amount, row.unit_price, row.seen_at = latest.amount, latest.unit_price, latest.scan.scanned_at
        row.trades = [
            {
                "account": s.scan.game_account.name or str(s.scan.game_account_id),
                "city": s.scan.city_name,
                "distance": s.distance,
                "url": _trade_url(row, s),
            }
            for s in sightings
            # an account does not trade with its own city
            if row.city_id not in own_cities.get(s.scan.game_account_id, set())
        ]
        rows.append(row)
    rows.sort(key=lambda o: (o.resource_idx, o.unit_price if kind == "sell" else -o.unit_price, o.distance))

    summary = []
    for idx, label in PublicMarketOffer.RESOURCE_CHOICES:
        all_rows = [o for o in rows if o.resource_idx == idx]
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
    if resource_idx is not None:
        # the cards keep every resource; only the list is narrowed
        rows = [o for o in rows if o.resource_idx == resource_idx]
    return {"scans": list(scans), "offers": rows, "summary": summary, "pending_scans": pending_scan_jobs()}
