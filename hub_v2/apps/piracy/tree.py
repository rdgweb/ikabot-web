"""The day's delivery tree (N-263): how every account's capture points reach a champion, through as many hops as needed.

A hop: A takes from B when they are of different lobbies, the pair A -> B is free (pair_days) and A beats B with the
month's chance (B holding min_points or more then: the bonus). The tree is drawn from the strongest down: each
account hangs on a champion or on an account already in the tree that beats it — so the strength grows towards the
champions, with as many hops as the accounts and lobbies call for. An account with direct_points or more goes
straight to a champion that may take it; below that it hangs on the shortest chain; with no chain it goes straight to
a champion from min_points. Between champions, the one that receives less gets the account.

Times: the champions take the accounts straight under them as soon as they can, and the ones that carry others at the
delivery (DELIVER_MARGIN before the day turns), one after the other; each carrier takes its own ones one after the
other just before it is taken. Conversion: an account stays beatable by whoever takes it (cap); one that can reach an
account no one reaches converts, a part at each mission, to the strength that does (the cheapest one is picked).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.utils import timezone

from . import flow
from .models import PiracyMember, PiracyRoute


@dataclass
class Node:
    ga: object
    champion: bool
    power: int | None                    # strength now (raiding)
    at_raid: int | None                  # strength when raided (holding min_points or more: the bonus)
    points: int
    parent: "Node | None" = None
    root: "Node | None" = None
    depth: int = 0
    children: list = field(default_factory=list)
    why: str = ""
    taken_at: datetime | None = None     # when its parent's raid lands on it
    send_at: datetime | None = None      # when its parent sends that raid
    collect_from: datetime | None = None # when it starts taking its own ones
    load: int = 0                        # points this champion gets in the tree (balance)

    @property
    def pk(self):
        return self.ga.pk

    @property
    def role(self) -> str:
        if self.champion:
            return PiracyMember.CHAMPION
        return PiracyMember.PASSER if self.children else PiracyMember.ORIGIN


@dataclass
class Tree:
    nodes: dict
    champions: list
    unreached: list
    deliver: datetime
    builders: dict                       # account pk -> {"target", "for", "convert"}

    def node(self, ga):
        return self.nodes.get(ga.pk)


def build(month, now: datetime | None = None, read: dict | None = None) -> Tree:
    """The tree of the day, from the accounts' strengths and points now and the pairs in use."""
    from . import services

    now = now or timezone.now()
    flow.join_new(month)
    stored = list(month.members.select_related("game_account__account").order_by("id"))
    read = read or {}
    nodes = {}
    for m in stored:
        ga = m.game_account
        st = read.get(ga.pk) or services.strength(ga)
        crew = st.get("crew")
        points = int(st.get("points") or 0)
        nodes[ga.pk] = Node(ga=ga, champion=m.role == PiracyMember.CHAMPION, power=crew,
                            at_raid=services.at_raid(month, crew, points) if crew is not None else None, points=points)
    champions = [n for n in nodes.values() if n.champion]
    for c in champions:
        c.root, c.depth = c, 0
        c.load = services.champion_takes(month).get(c.pk, 0)
    # a raid decided or on its way keeps its target on that raider (the tree drawn again never takes it elsewhere)
    active = dict(PiracyRoute._base_manager.filter(month=month, status__in=(PiracyRoute.PLANNED, PiracyRoute.SENT),
                                                   target__isnull=False).values_list("target_id", "raider_id"))
    pair = {}

    def can(raider: Node, target: Node) -> bool:
        if raider.ga.account_id == target.ga.account_id or raider.power is None or target.at_raid is None:
            return False
        key = (raider.pk, target.pk)
        if key not in pair:
            pair[key] = services.pair_release(month, raider.ga, target.ga, now) is None
        return pair[key] and services.sure_win(raider.power, target.at_raid, month.min_win_percent)

    others = sorted([n for n in nodes.values() if not n.champion and n.power is not None], key=lambda n: -n.power)
    unreached = [n for n in nodes.values() if not n.champion and n.power is None]
    direct_from = max(month.direct_points or 0, month.min_points)
    def by_load(heads):
        """The champion that gets less (what it took this month plus what the day's tree sends it) first."""
        return sorted(heads, key=lambda c: (c.load, -(c.power or 0)))

    for v in others:
        raider = nodes.get(active.get(v.pk))
        if (raider is not None and (raider.champion or raider.root is not None) and raider.ga.account_id != v.ga.account_id
                and raider.power is not None and v.at_raid is not None
                and services.sure_win(raider.power, v.at_raid, month.min_win_percent)):
            v.parent, v.root, v.depth, v.why = raider, raider.root, raider.depth + 1, "assalto a caminho"
            raider.children.append(v)
            v.root.load += v.points
            continue
        heads = [c for c in champions if can(c, v)]
        chains = [u for u in others if u.root is not None and u is not v and can(u, v)]
        if not heads and not chains:
            unreached.append(v)
            v.why = "sem caminho hoje"
            continue
        # the side of the champion that gets less in the day's tree first; on that side, the X rule
        roots = {c.root.pk: c.root for c in heads + chains}
        root = by_load(list(roots.values()))[0]
        heads = [c for c in heads if c.pk == root.pk]
        chains = sorted([u for u in chains if u.root.pk == root.pk], key=lambda u: (u.depth, -u.power))
        if heads and (v.points >= direct_from or not chains):
            parent = heads[0]
            v.why = "direto" if v.points >= direct_from else "direto (sem corrente)"
        else:
            parent = chains[0]
            v.why = "corrente"
        v.parent, v.root, v.depth = parent, parent.root, parent.depth + 1
        parent.children.append(v)
        v.root.load += v.points
    deliver = flow.deliver_at(month, now)
    _times(champions, deliver, now)
    tree = Tree(nodes=nodes, champions=champions, unreached=unreached, deliver=deliver, builders={})
    _builders(month, tree, now, can)
    return tree


def _times(champions, deliver, now) -> None:
    """When each hop happens. A raid goes out at send_at, lands at taken_at (half the way) and the ship is back a hop
    later. A champion takes the accounts straight under it at once and the carriers one after the other, the last one
    landing at the delivery; a carrier takes its own ones one after the other, its ship back when it is taken."""
    def place(parent: Node, back_by: datetime):
        hop = timedelta(seconds=flow.raid_seconds(parent.ga))
        order = [c for c in parent.children if c.children] if parent.champion else             [c for c in parent.children if c.children] + [c for c in parent.children if not c.children]
        if parent.champion:
            for leaf in (c for c in parent.children if not c.children):   # straight under a champion: at once
                leaf.send_at, leaf.taken_at = now, now + hop / 2
        else:
            parent.collect_from = back_by - hop * len(order)
        start = back_by - hop * len(order)
        for i, child in enumerate(order):
            child.send_at = start + hop * i
            child.taken_at = child.send_at + hop / 2
            if child.children:
                place(child, child.taken_at)                 # its own ones are in before it is taken
    for c in champions:
        place(c, deliver + timedelta(seconds=flow.raid_seconds(c.ga)) / 2)


def _builders(month, tree: Tree, now, can) -> None:
    """For an account no chain reaches: the account of the tree that reaches it converting the least, without going
    above what its own taker beats — it converts that, a part at each mission."""
    from . import services

    ratio = services.ratio_for(month.min_win_percent)
    for v in tree.unreached:
        if v.at_raid is None or v.points < month.min_points // 2:
            continue
        target = int(v.at_raid * ratio) + 1
        best = None
        for u in tree.nodes.values():
            if u.champion or u.root is None or u.ga.account_id == v.ga.account_id or u.power is None:
                continue
            if services.pair_release(month, u.ga, v.ga, now):
                continue
            cap = cap_of(month, u)
            if cap is not None and target > cap:
                continue
            base = u.power - services.crew_bonus(u.points)
            cap_base = cap - services.CREW_BONUS_MAX if cap is not None else None
            convert = services.convert_for(base, u.points, target, cap_base)
            if convert is not None and (best is None or convert < best[0]):
                best = (convert, u)
        if best is not None:
            convert, u = best
            prev = tree.builders.get(u.pk)
            if prev is None or target > prev["target"]:
                tree.builders[u.pk] = {"target": target, "for": v.ga, "convert": convert}


def cap_of(month, node: Node) -> int | None:
    """The strength this account may have so whoever takes it still wins (None: nobody takes it today)."""
    from . import services

    if node.parent is None or node.parent.power is None:
        return None
    return int(node.parent.power / services.ratio_for(month.min_win_percent))


def due(tree: Tree, raider, now: datetime | None = None) -> tuple[list, datetime | None]:
    """The accounts this raider takes now (ready) and, when none is ready, when the next one is."""
    now = now or timezone.now()
    node = tree.node(raider)
    if node is None:
        return [], None
    ready, later = [], []
    for child in node.children:
        if child.send_at is None or child.send_at <= now:
            ready.append(child)
        else:
            later.append(child.send_at)
    ready.sort(key=lambda c: -c.points)
    return ready, (min(later) if later else None)
