"""Opaque token generation and hashing. Only sha256 digests are stored."""

import hashlib
import secrets

ACCESS_TTL = 3600            # 1 hour
REFRESH_TTL = 90 * 86400     # 90 days
CODE_TTL = 600               # 10 minutes
INVITE_TTL = 7 * 86400       # 7 days
TXN_TTL = 900                # 15 minutes for the /authorize -> /login handoff
ROTATE_GRACE = 60            # seconds a rotated refresh token remains acceptable (Claude retry races)

# Unambiguous alphabet for human-typed invite codes (no 0/O/1/I/L).
_INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
