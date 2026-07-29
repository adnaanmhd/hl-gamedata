"""
Contributor identity masking.

We never store contributor emails. Instead, we derive a stable per-contributor
ID via HMAC-SHA256(email, SECRET) and use that everywhere — folder names,
metadata, logs.

Properties:
    - Deterministic: same email -> same ID, always.
    - Irreversible: given an ID, you cannot recover the email without the
      secret AND a candidate list of emails to brute-force against.
    - Collision-resistant: 64-bit truncation gives ~1 in 4 billion collision
      probability between any two emails.

Threat model: prevents casual leakage of session data from revealing
contributor emails. Does NOT defend against an attacker who reverse-engineers
the .exe to extract SECRET — for that level of guarantee, masking should
happen server-side after upload. v0 ships with client-side masking; that's
fine for current scale.

Rotating SECRET breaks all join-keys for old sessions. Treat it as immutable
once deployed.
"""
from __future__ import annotations

import hashlib
import hmac
import re

_SECRET = b"humyn-labs-contributor-id-v1"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(email: str) -> bool:
    """Lightweight email format check. Not RFC-strict; catches typos."""
    return bool(_EMAIL_RE.match(email.strip()))


def mask_email(email: str) -> str:
    """
    Convert a contributor email to its stable masked ID.

    Returns: 'c_' + 16 hex chars (64-bit). Total length: 18 chars.

    Raises ValueError if email format is invalid.
    """
    cleaned = email.strip().lower()
    if not is_valid_email(cleaned):
        raise ValueError(f"not a valid email: {email!r}")
    digest = hmac.new(_SECRET, cleaned.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"c_{digest[:16]}"
