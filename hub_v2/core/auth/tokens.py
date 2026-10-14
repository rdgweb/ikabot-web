"""Agent keys (N-106, stage 3b): the hub keeps only a hash, never the key itself.

A copy of the database must not be enough to impersonate an agent and ask the API for the
decrypted game passwords. Keys are long random strings, so a plain SHA-256 is enough.
"""
import hashlib
import hmac
import secrets


def new_agent_token() -> str:
    return f"tok_{secrets.token_hex(24)}"


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def agent_token_matches(token: str, token_hash: str) -> bool:
    return bool(token and token_hash) and hmac.compare_digest(hash_agent_token(token), token_hash)
