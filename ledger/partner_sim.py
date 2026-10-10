"""SIMULATED partner system. It holds a callback link and calls it the way real partner
systems often do: retries with the same idempotency key, replies after the timeout, or
(here, an attacker) sends a body signed with the wrong secret. Synthetic data only."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

from . import signing


def _post(base_url, path, body: bytes, headers: dict):
    started = time.perf_counter()
    r = requests.post(base_url.rstrip("/") + path, data=body, headers=headers, timeout=30)
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw": r.text[:200]}
    return {
        "http": r.status_code,
        "body": payload,
        "replayed": r.headers.get("Idempotent-Replayed") == "true",
        "ms": round((time.perf_counter() - started) * 1000),
    }


def build(payload: dict, key: str, secret: str | None = None, timestamp: int | None = None):
    body = json.dumps(payload, sort_keys=True).encode()
    headers = {"Content-Type": "application/json", "Idempotency-Key": key}
    headers.update(signing.sign_body(body, timestamp, secret))
    return body, headers


def retry_storm(base_url, path, n=10, decision="approved"):
    """n copies of the same reply, released at the same instant from n threads."""
    key = f"dealer-review-{uuid.uuid4().hex[:8]}"
    body, headers = build({"data": {"decision": decision, "reviewer": "Dealer desk (simulated)"}}, key)
    gate = threading.Barrier(n)

    def one(_):
        gate.wait()
        return _post(base_url, path, body, headers)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return key, list(pool.map(one, range(n)))


def single(base_url, path, decision="approved", key=None):
    key = key or f"dealer-review-{uuid.uuid4().hex[:8]}"
    body, headers = build({"data": {"decision": decision, "reviewer": "Dealer desk (simulated)"}}, key)
    return key, _post(base_url, path, body, headers)


def forged(base_url, path):
    """Signed with a secret the partner never had."""
    key = f"dealer-review-{uuid.uuid4().hex[:8]}"
    body, headers = build({"data": {"decision": "approved", "amount": 99000}}, key, secret="not-the-shared-secret")
    return key, _post(base_url, path, body, headers)
