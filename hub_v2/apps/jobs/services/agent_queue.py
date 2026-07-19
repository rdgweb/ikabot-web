"""Inspeciona/purga as filas Celery (Redis) dos nós agents.

Cada job vira uma task Celery roteada para a fila com nome = node_id. No Redis:
  - LISTA (chave = node_id): tarefas imediatas aguardando entrega;
  - `unacked` (+ `unacked_index`): tarefas ETA/reservadas seguradas por um worker.

Limpar os Jobs no banco NAO remove isso — as mensagens ficam presas no broker.
Usado pelo management command e pela UI de nós.
"""
from __future__ import annotations

import json

import redis
from django.conf import settings


def _decode(v):
    return v.decode() if isinstance(v, (bytes, bytearray)) else v


def _client():
    return redis.Redis.from_url(settings.CELERY_BROKER_URL)


def _list_len(r, key: str) -> int:
    try:
        if _decode(r.type(key)) != "list":
            return 0
        return int(r.llen(key))
    except Exception:
        return 0


def unacked_by_node(r=None) -> dict[str, list[str]]:
    """{routing_key(node_id): [delivery_tag, ...]} — tarefas reservadas/ETA."""
    r = r or _client()
    out: dict[str, list[str]] = {}
    try:
        h = r.hgetall("unacked")
    except Exception:
        return out
    for tag, raw in h.items():
        tag = _decode(tag)
        try:
            d = json.loads(_decode(raw))
            rk = str(d[2]) if isinstance(d, list) and len(d) >= 3 else ""
        except Exception:
            rk = ""
        out.setdefault(rk, []).append(tag)
    return out


def node_queue_stats(node_id: str) -> dict:
    """Fila imediata + unacked de um nó."""
    r = _client()
    umap = unacked_by_node(r)
    return {
        "queue_len": _list_len(r, str(node_id)),
        "unacked": len(umap.get(str(node_id), [])),
    }


def all_queue_stats(node_ids: list[str]) -> dict[str, dict]:
    r = _client()
    umap = unacked_by_node(r)
    return {
        str(nid): {"queue_len": _list_len(r, str(nid)), "unacked": len(umap.get(str(nid), []))}
        for nid in node_ids
    }


def purge_node(node_id: str) -> dict:
    """Remove a fila imediata e as entradas unacked do nó. Retorna contagens."""
    r = _client()
    node_id = str(node_id)
    list_n = _list_len(r, node_id)
    if list_n:
        r.delete(node_id)
    tags = unacked_by_node(r).get(node_id, [])
    if tags:
        r.hdel("unacked", *tags)
        try:
            r.zrem("unacked_index", *tags)
        except Exception:
            pass
    return {"queue_len": list_n, "unacked": len(tags)}
