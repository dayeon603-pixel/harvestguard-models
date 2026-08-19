"""Device signing and the tamper-evident chain.

Two separate guarantees, deliberately not conflated:

* **Device signature** proves a pod produced an event. Keys are per-device and never leave
  the backend or the device's secure storage, so a stolen pod compromises one pod's history
  and nothing else.
* **Hash chain** proves nothing was altered after the fact. Each record commits to the
  previous record's hash, so editing any historical record invalidates every record after it.

A lender needs both. A signature alone lets an operator silently delete inconvenient records;
a chain alone lets anyone write records that were never observed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

__all__ = ["GENESIS_HASH", "new_device_key", "sign", "verify", "chain_hash"]

GENESIS_HASH: Final[str] = "0" * 64
_ENCODING: Final[str] = "utf-8"


def new_device_key() -> str:
    """Provision a fresh 256-bit device key, hex-encoded."""
    return secrets.token_hex(32)


def sign(device_key_hex: str, canonical: str) -> str:
    """HMAC-SHA256 over an event's canonical form."""
    key = bytes.fromhex(device_key_hex)
    return hmac.new(key, canonical.encode(_ENCODING), hashlib.sha256).hexdigest()


def verify(device_key_hex: str, canonical: str, signature: str) -> bool:
    """Constant-time signature check."""
    try:
        expected = sign(device_key_hex, canonical)
    except ValueError:
        return False
    return hmac.compare_digest(expected, signature)


def chain_hash(prev_hash: str, canonical: str, signature: str) -> str:
    """Commit a record to the chain: H(prev || canonical || signature)."""
    h = hashlib.sha256()
    h.update(prev_hash.encode(_ENCODING))
    h.update(b"\x1f")
    h.update(canonical.encode(_ENCODING))
    h.update(b"\x1f")
    h.update(signature.encode(_ENCODING))
    return h.hexdigest()
