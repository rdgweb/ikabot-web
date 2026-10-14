"""Requests from the hub to the agent that owns a game session (N-72).

The agent is the only one that talks to the game: it keeps the cookies and goes
out through the account's proxy. The hub cannot connect to an agent (agents may
be on other hosts, behind NAT), but every agent already holds a connection to
the broker, so the request travels there:

    hub    LPUSH ikabot:manual:req:<node_id>   {"id": ..., "op": ..., ...}
    agent  BRPOP that list, does the work with its own session
    agent  RPUSH ikabot:manual:resp:<id>       {"ok": ..., ...}   (expires by itself)
    hub    BLPOP the answer

Nothing here carries cookies or credentials. The one address that travels is the exit
address of a manual-control session, which the operator asked to see (N-99).
"""

from __future__ import annotations

import json
import time
import uuid

import redis
from django.conf import settings

REQUEST_KEY = "ikabot:manual:req:{node_id}"
RESPONSE_KEY = "ikabot:manual:resp:{request_id}"
# N-106 3b-2: which node a request was sent to — an agent over HTTPS answers only its own
OWNER_KEY = "ikabot:manual:owner:{request_id}"
# A request nobody picked up is worthless after this long; the agent drops it.
REQUEST_TTL_SECONDS = 60
# Requests of a node whose agent is gone must not pile up forever.
QUEUE_TTL_SECONDS = 300

_client = None


def client():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
    return _client


def send(node_id, payload: dict, *, ttl: int = REQUEST_TTL_SECONDS) -> str:
    """Queue a request for the agent of ``node_id``; returns its id."""
    request_id = uuid.uuid4().hex
    message = dict(payload)
    message["id"] = request_id
    message["expires"] = time.time() + ttl
    key = REQUEST_KEY.format(node_id=node_id)
    connection = client()
    connection.set(OWNER_KEY.format(request_id=request_id), str(node_id), ex=max(ttl, 1) + 60)
    connection.lpush(key, json.dumps(message))
    connection.expire(key, QUEUE_TTL_SECONDS)
    return request_id


def next_for(node_id, *, timeout: int):
    """Over HTTPS (N-106 3b-2): the next request for this node, waiting up to ``timeout`` seconds."""
    found = client().brpop(REQUEST_KEY.format(node_id=node_id), timeout=max(1, int(timeout)))
    return _decode(found[1]) if found else None


def pending(node_id) -> bool:
    return bool(client().llen(REQUEST_KEY.format(node_id=node_id)))


def answer_from(node_id, request_id: str, answer: dict, *, ttl: int = 120) -> bool:
    """Store an agent's answer, only if the request was sent to that agent's node."""
    connection = client()
    owner = connection.get(OWNER_KEY.format(request_id=request_id))
    if owner is None or owner.decode() != str(node_id):
        return False
    key = RESPONSE_KEY.format(request_id=request_id)
    connection.rpush(key, json.dumps(answer))
    connection.expire(key, ttl)
    return True


def _decode(raw) -> dict | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def poll(request_id: str) -> dict | None:
    """The answer if it has already arrived, without waiting."""
    return _decode(client().lpop(RESPONSE_KEY.format(request_id=request_id)))


def wait(request_id: str, *, timeout: float) -> dict | None:
    """Wait for the answer; None when the agent did not answer in time."""
    found = client().blpop(RESPONSE_KEY.format(request_id=request_id), timeout=max(1, int(timeout)))
    return _decode(found[1]) if found else None


def call(node_id, payload: dict, *, timeout: float) -> dict | None:
    return wait(send(node_id, payload, ttl=int(timeout)), timeout=timeout)
