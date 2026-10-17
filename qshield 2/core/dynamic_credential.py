"""Dynamic one-time credential (OTC) for large payments.

Flow (matches the design notes):

  1. The user starts a transaction above the threshold.
  2. issue()  creates a TASK-SPECIFIC credential: a random 6-digit code that is
              bound to the user, the merchant, the amount, the currency and a
              unique transaction ID, with an expiry time.
  3. The binding is CRYPTOGRAPHICALLY PROTECTED: the server keeps only an
     HMAC-SHA256 tag over (user, merchant, amount, currency, txn_id, expiry, code),
     made with a secret server key. The code itself is never stored.
  4. verify() checks authenticity (the tag), the transaction details, the expiry
              and the usage count (once only, limited wrong guesses).
  5. On success the credential is CONSUMED and can never be used again.

Because the tag covers the amount, a code issued for Rs 12,000 fails for Rs 90,000,
and a code for one user or merchant fails for another.

This is a SIMULATION of delivery: the demo shows the code on screen. In a real
deployment the code goes to the user's trusted device (see the QShield device-key
design), never to the same screen that is paying.

Not built here (needs accounts and a database): rate limits per user across many
transactions, and persistence across server restarts. The store below lives in
the server process only, like MemoryTokenStore.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass

# Rs 10,000 in paise. Verification is required when the amount is ABOVE this.
OTP_THRESHOLD_PAISE = 1_000_000
OTP_DIGITS = 6
TTL_SECONDS = 300      # a code is valid for 5 minutes
MAX_ATTEMPTS = 3       # wrong guesses before the credential locks
MAX_ISSUES = 3         # codes (including "resend") per transaction
FORMAT_VERSION = b"qshield-otc-v1"
_KEEP_AFTER_EXPIRY = 3600  # forget finished records after an hour


class CredentialError(Exception):
    """Plain-English problem with a verification code."""


@dataclass(frozen=True)
class IssuedCredential:
    txn_id: str
    otp: str            # show/deliver this once; it is not stored by the store
    expires_at: float   # unix seconds


@dataclass
class _Record:
    tag: str            # HMAC over the bound details + code (never the code itself)
    expires_at: float
    attempts: int = 0
    issues: int = 1
    status: str = "pending"  # pending | consumed | locked


def requires_verification(amount_paise: int) -> bool:
    """True when this payment needs the extra check (strictly above Rs 10,000)."""
    return amount_paise > OTP_THRESHOLD_PAISE


# ----------------------------------------------------------------- crypto helpers

_PROCESS_KEY = secrets.token_bytes(32)  # used when no server key is configured


def _server_key() -> bytes:
    """Secret HMAC key. Set QSHIELD_OTC_KEY (32+ characters) on the server for a
    stable key. Without it a random key is made per process, which is fine for the
    in-memory demo because the records are lost on restart anyway."""
    configured = os.environ.get("QSHIELD_OTC_KEY", "")
    if configured:
        if len(configured) < 32:
            raise CredentialError("QSHIELD_OTC_KEY must be at least 32 characters.")
        return configured.encode()
    return _PROCESS_KEY


def _new_otp() -> str:
    return f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"


def _pack(*parts: bytes | str) -> bytes:
    """Length-prefix every field so ('ab','c') and ('a','bc') can never collide."""
    out = b""
    for part in parts:
        raw = part if isinstance(part, bytes) else str(part).encode()
        out += len(raw).to_bytes(4, "big") + raw
    return out


def _tag(user_id: str, merchant: str, amount_paise: int, currency: str,
         txn_id: str, expires_at: float, otp: str) -> str:
    message = _pack(FORMAT_VERSION, user_id, merchant, amount_paise, currency,
                    txn_id, int(expires_at), otp)
    return hmac.new(_server_key(), message, hashlib.sha256).hexdigest()


def _check_details(user_id, merchant, amount_paise, currency, txn_id) -> None:
    for name, value in (("user", user_id), ("merchant", merchant),
                        ("currency", currency), ("transaction ID", txn_id)):
        if not isinstance(value, str) or not value:
            raise CredentialError(f"Missing {name} for the verification code.")
    if not isinstance(amount_paise, int) or isinstance(amount_paise, bool) or amount_paise <= 0:
        raise CredentialError("Amount must be a positive whole number of paise.")


# ------------------------------------------------------------------------- store

class MemoryCredentialStore:
    name = "In-memory (NOT persistent: lost when the server restarts)"
    persistent = False

    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    def issue(self, *, user_id: str, merchant: str, amount_paise: int, currency: str,
              txn_id: str, now: float | None = None) -> IssuedCredential:
        """Create (or re-create, for "resend") the code for one transaction."""
        _check_details(user_id, merchant, amount_paise, currency, txn_id)
        now = time.time() if now is None else now
        with self._lock:
            self._purge(now)
            existing = self._records.get(txn_id)
            if existing is not None and existing.status == "consumed":
                raise CredentialError("This payment was already verified. Start a new payment.")
            if existing is not None and existing.issues >= MAX_ISSUES:
                raise CredentialError("Too many codes requested for this payment. Start a new payment.")
            otp = _new_otp()
            expires_at = now + TTL_SECONDS
            self._records[txn_id] = _Record(
                tag=_tag(user_id, merchant, amount_paise, currency, txn_id, expires_at, otp),
                expires_at=expires_at,
                issues=(existing.issues + 1) if existing else 1,
            )  # a new code replaces the old one, so the old code stops working
            return IssuedCredential(txn_id, otp, expires_at)

    def verify(self, *, user_id: str, merchant: str, amount_paise: int, currency: str,
               txn_id: str, otp: str, now: float | None = None) -> None:
        """Check the code against the details of the payment being made right now.
        Returns None on success (the credential is then consumed); raises
        CredentialError otherwise."""
        _check_details(user_id, merchant, amount_paise, currency, txn_id)
        now = time.time() if now is None else now
        code = (otp or "").strip()
        if not (len(code) == OTP_DIGITS and code.isdigit()):
            raise CredentialError(f"Enter the {OTP_DIGITS}-digit code.")  # not counted as a guess
        with self._lock:  # one lock: check + consume is atomic, so a code works once even under races
            rec = self._records.get(txn_id)
            if rec is None:
                raise CredentialError("No code was requested for this payment. Request a new one.")
            if rec.status == "consumed":
                raise CredentialError("This code was already used.")
            if rec.status == "locked":
                raise CredentialError("Too many wrong codes. Request a new code.")
            if now >= rec.expires_at:
                raise CredentialError("This code has expired. Request a new one.")
            expected = _tag(user_id, merchant, amount_paise, currency, txn_id, rec.expires_at, code)
            if not hmac.compare_digest(expected, rec.tag):
                rec.attempts += 1
                if rec.attempts >= MAX_ATTEMPTS:
                    rec.status = "locked"
                    raise CredentialError("Too many wrong codes. Request a new code.")
                left = MAX_ATTEMPTS - rec.attempts
                raise CredentialError(
                    f"Wrong code, or the payment details changed. {left} attempt(s) left.")
            rec.status = "consumed"

    def _purge(self, now: float) -> None:
        stale = [k for k, r in self._records.items() if now > r.expires_at + _KEEP_AFTER_EXPIRY]
        for key in stale:
            del self._records[key]


_MEMORY = MemoryCredentialStore()


def get_store() -> MemoryCredentialStore:
    return _MEMORY
