"""Callback link tokens and partner request signatures. Both use HMAC-SHA256 and
constant-time comparison."""
import base64
import hashlib
import hmac
import time
import uuid

from django.conf import settings


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _link_mac(wait_id: uuid.UUID, expires: int) -> str:
    msg = f"{wait_id.hex}.{expires}".encode()
    return _b64(hmac.new(settings.CALLBACK_LINK_SECRET.encode(), msg, hashlib.sha256).digest())


def make_link_token(wait_id: uuid.UUID, expires: int) -> str:
    return f"{wait_id.hex}.{expires}.{_link_mac(wait_id, expires)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_link_token(token: str):
    """Return (wait_id, expires_epoch) if the token was minted by us, else None.
    Expiry is checked by the caller so an expired link can get its own 410 answer."""
    try:
        wait_hex, expires_s, mac = token.split(".")
        wait_id = uuid.UUID(hex=wait_hex)
        expires = int(expires_s)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(mac, _link_mac(wait_id, expires)):
        return None
    return wait_id, expires


def sign_body(body: bytes, timestamp: int | None = None, secret: str | None = None) -> dict:
    """Headers a partner sends: X-Ledger-Timestamp and X-Ledger-Signature: v1=<hex>."""
    ts = int(timestamp if timestamp is not None else time.time())
    key = (secret or settings.PARTNER_SIGNING_SECRET).encode()
    mac = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Ledger-Timestamp": str(ts), "X-Ledger-Signature": f"v1={mac}"}


def check_body_signature(body: bytes, timestamp: str | None, signature: str | None, now: float) -> str | None:
    """Return None if valid, otherwise 'stale' or 'bad'."""
    try:
        ts = int(timestamp or "")
    except ValueError:
        return "stale"
    if abs(now - ts) > settings.SIGNATURE_TOLERANCE_SECONDS:
        return "stale"
    expected = sign_body(body, ts)["X-Ledger-Signature"]
    if not signature or not hmac.compare_digest(signature.encode(), expected.encode()):
        return "bad"
    return None
