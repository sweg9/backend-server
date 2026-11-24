"""Identifiers: a prefix and a ULID, for example `ctl_01JD3Q...` (wiki: Database).

ULIDs sort by creation time and are never reused. Implemented here to avoid a dependency.
"""

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32

PREFIXES = {"ctl", "dev", "mp", "rt", "r", "sc", "cr", "use", "fb", "m", "sit", "vis"}


def ulid(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    n = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}")
    return f"{prefix}_{ulid()}"
