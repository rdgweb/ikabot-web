"""
Fernet symmetric encryption for sensitive fields (passwords, tokens).
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings


@lru_cache(maxsize=1)
def _build_fernet() -> Fernet:
    secret = settings.APP_SECRET.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64 token."""
    return _build_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a base64 token and return plaintext."""
    return _build_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
