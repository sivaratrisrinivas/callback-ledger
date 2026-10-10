import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from ledger import partner_sim, services
from ledger.models import Application


@pytest.fixture
def make_app():
    def _make(ref="APP-1"):
        return Application.objects.create(ref=ref, borrower_name="Maya Ortiz", dealer_name="Harbor Auto (synthetic)",
                                          amount=Decimal("22900"))
    return _make


@pytest.fixture
def waiting(make_app):
    """An application paused on a wait step with a 60 s maximum wait."""
    app = make_app()
    t0 = timezone.now()
    wait, path = services.open_wait(app, "Wait for webhook: dealer review", 60, now=t0)
    return app, wait, path, t0


def token_of(path):
    return path.rsplit("/", 1)[1]


def call(path, key, decision="approved", at=None, secret=None, ts=None, payload=None):
    """Call the ledger in-process with a correctly signed body (unless secret/ts say otherwise)."""
    at = at or timezone.now()
    body, headers = partner_sim.build(payload or {"data": {"decision": decision}}, key, secret=secret,
                                      timestamp=ts if ts is not None else int(at.timestamp()))
    return services.handle_callback(token_of(path), body, headers, "test", received_at=at)


def in_threads(fns):
    """Run callables at the same instant, each on its own DB connection."""
    import threading

    gate = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        try:
            gate.wait()
            out[i] = fn()
        except Exception as exc:  # surface errors in the main thread
            out[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    errors = [o for o in out if isinstance(o, Exception)]
    assert not errors, errors
    return out


def seconds(n):
    return timedelta(seconds=n)
