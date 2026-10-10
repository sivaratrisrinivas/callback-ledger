"""Plain pytest tests for the wait/resume ledger. Run on SQLite by default and on Postgres
with DATABASE_URL=postgres://... (the target database)."""
import json

import pytest
from django.utils import timezone

from ledger import partner_sim, services
from ledger.models import Application, CallbackEvent, RejectedCallback, TimelineEntry, WaitStep

from .conftest import call, in_threads, seconds, token_of

pytestmark = pytest.mark.django_db(transaction=True)


def refreshed(app, wait):
    app.refresh_from_db()
    wait.refresh_from_db()
    return app, wait


def test_in_time_callback_resumes_once(waiting):
    app, wait, path, t0 = waiting
    r = call(path, "k1", at=t0 + seconds(5))
    assert r.status == 200 and r.body["status"] == "resumed"
    app, wait = refreshed(app, wait)
    assert wait.state == WaitStep.State.RESUMED
    assert app.status == Application.Status.RESUMED and app.stage == "Offer ready"
    assert app.advance_count == 1


def test_ten_concurrent_duplicates_resume_exactly_once(waiting):
    app, wait, path, t0 = waiting
    replies = in_threads([lambda: call(path, "dealer-review-1", at=t0 + seconds(5))] * 10)
    assert [r.status for r in replies] == [200] * 10
    assert all(r.body == replies[0].body for r in replies)  # every retry sees the original answer
    assert sum(1 for r in replies if r.headers.get("Idempotent-Replayed")) == 9
    app, wait = refreshed(app, wait)
    assert app.advance_count == 1
    ev = CallbackEvent.objects.get(wait=wait)
    assert ev.outcome == CallbackEvent.Outcome.APPLIED and ev.retry_count == 9


def test_retry_storm_over_real_http(live_server, waiting):
    """The simulated partner fires 10 identical signed requests from 10 threads at a live server."""
    app, wait, path, t0 = waiting
    key, results = partner_sim.retry_storm(live_server.url, path, n=10)
    assert sorted(r["http"] for r in results) == [200] * 10
    assert sum(r["replayed"] for r in results) == 9
    app.refresh_from_db()
    assert app.advance_count == 1


def test_callback_after_timeout_is_parked_not_applied(waiting):
    app, wait, path, t0 = waiting
    assert services.expire_wait(wait.pk, now=t0 + seconds(61)) is True
    r = call(path, "late-1", at=t0 + seconds(90))
    assert r.status == 202 and r.body["status"] == "parked"
    app, wait = refreshed(app, wait)
    assert wait.state == WaitStep.State.TIMED_OUT
    assert app.status == Application.Status.TIMEOUT_PATH and app.advance_count == 0
    ev = CallbackEvent.objects.get(wait=wait)
    assert ev.outcome == CallbackEvent.Outcome.LATE_PARKED and ev.agent_status == "OPEN"


def test_late_callback_runs_timeout_path_if_timer_has_not_fired(waiting):
    """Broker down or worker asleep: the late reply still never advances the file."""
    app, wait, path, t0 = waiting
    r = call(path, "late-1", at=t0 + seconds(120))
    assert r.status == 202
    app, wait = refreshed(app, wait)
    assert wait.state == WaitStep.State.TIMED_OUT and app.advance_count == 0
    assert services.sweep_expired(now=t0 + seconds(130)) == 0  # nothing left to time out


def test_reply_at_the_exact_deadline_counts_as_late(waiting):
    app, wait, path, t0 = waiting
    r = call(path, "edge-1", at=wait.timeout_at)
    assert r.status == 202
    app, _ = refreshed(app, wait)
    assert app.advance_count == 0


def test_timeout_and_callback_racing_at_the_same_instant(make_app):
    """Timeout task and an in-time callback fire together, 15 times. Exactly one of them wins
    each time, and the file never moves twice."""
    for i in range(15):
        app = make_app(ref=f"APP-R{i}")
        t0 = timezone.now()
        wait, path = services.open_wait(app, "Wait for webhook: dealer review", 1, now=t0)
        deadline = wait.timeout_at
        in_threads([
            lambda: services.expire_wait(wait.pk, now=deadline),
            lambda: call(path, f"race-{i}", at=deadline - seconds(0.001)),
        ])
        app, wait = refreshed(app, wait)
        events = list(CallbackEvent.objects.filter(wait=wait))
        assert len(events) == 1
        if wait.state == WaitStep.State.RESUMED:
            assert events[0].outcome == "APPLIED" and app.advance_count == 1
        else:
            assert wait.state == WaitStep.State.TIMED_OUT
            assert events[0].outcome == "LATE_PARKED" and app.advance_count == 0


def test_forged_signature_is_rejected_and_logged(waiting):
    app, wait, path, t0 = waiting
    r = call(path, "k1", at=t0 + seconds(5), secret="attacker-guess")
    assert r.status == 401 and r.body["reason"] == "BAD_SIGNATURE"
    app, wait = refreshed(app, wait)
    assert wait.state == WaitStep.State.WAITING and app.advance_count == 0
    assert not CallbackEvent.objects.exists()
    assert RejectedCallback.objects.get().reason == "BAD_SIGNATURE"


def test_tampered_callback_link_is_rejected(waiting):
    app, wait, path, t0 = waiting
    token = token_of(path)
    bad = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    body, headers = partner_sim.build({"data": {"decision": "approved"}}, "k1")
    r = services.handle_callback(bad, body, headers, "test", received_at=t0 + seconds(5))
    assert r.status == 401 and r.body["reason"] == "BAD_LINK"
    assert RejectedCallback.objects.get().reason == "BAD_LINK"


def test_replayed_old_signed_request_is_rejected(waiting):
    app, wait, path, t0 = waiting
    old_ts = int((t0 - seconds(3600)).timestamp())
    r = call(path, "k1", at=t0 + seconds(5), ts=old_ts)
    assert r.status == 401 and r.body["reason"] == "STALE_TIMESTAMP"


def test_expired_link_gets_410(make_app):
    app = make_app()
    t0 = timezone.now()
    wait, path = services.open_wait(app, "Wait for webhook: dealer review", 60, now=t0, link_ttl_seconds=300)
    r = call(path, "k1", at=t0 + seconds(301))
    assert r.status == 410 and r.body["reason"] == "LINK_EXPIRED"
    assert not CallbackEvent.objects.exists()


def test_second_answer_with_a_new_key_after_resume_is_parked(waiting):
    """Out-of-order keys: B arrives after A resumed the flow; a retry of A still replays A."""
    app, wait, path, t0 = waiting
    assert call(path, "A", at=t0 + seconds(2)).status == 200
    rb = call(path, "B", decision="declined", at=t0 + seconds(3))
    assert rb.status == 202 and rb.body["status"] == "parked"
    ra = call(path, "A", at=t0 + seconds(4))
    assert ra.status == 200 and ra.headers.get("Idempotent-Replayed") == "true"
    app, wait = refreshed(app, wait)
    assert app.advance_count == 1 and app.stage == "Offer ready"
    assert CallbackEvent.objects.get(idempotency_key="B").outcome == "EXTRA_PARKED"


def test_same_key_with_a_different_body_is_refused(waiting):
    app, wait, path, t0 = waiting
    assert call(path, "A", at=t0 + seconds(2)).status == 200
    r = call(path, "A", decision="declined", at=t0 + seconds(3))
    assert r.status == 422 and r.body["reason"] == "KEY_REUSED"
    app.refresh_from_db()
    assert app.stage == "Offer ready"


def test_missing_idempotency_key_is_refused(waiting):
    app, wait, path, t0 = waiting
    body, headers = partner_sim.build({"data": {"decision": "approved"}}, "x")
    headers.pop("Idempotency-Key")
    r = services.handle_callback(token_of(path), body, headers, "test", received_at=t0 + seconds(1))
    assert r.status == 400 and r.body["reason"] == "MISSING_KEY"


def test_sweeper_is_idempotent(waiting):
    app, wait, path, t0 = waiting
    assert services.sweep_expired(now=t0 + seconds(30)) == 0
    assert services.sweep_expired(now=t0 + seconds(61)) == 1
    assert services.sweep_expired(now=t0 + seconds(62)) == 0
    assert TimelineEntry.objects.filter(application=app, kind="timeout").count() == 1


def test_agent_applies_a_late_reply_once(waiting):
    app, wait, path, t0 = waiting
    services.expire_wait(wait.pk, now=t0 + seconds(61))
    call(path, "late-1", at=t0 + seconds(70))
    call(path, "late-2", decision="declined", at=t0 + seconds(71))
    e1, e2 = CallbackEvent.objects.order_by("received_at")
    services.agent_decide(e1.pk, "apply", "Ops agent", "dealer confirmed by phone")
    app, wait = refreshed(app, wait)
    assert app.status == Application.Status.AGENT_RESOLVED and app.advance_count == 1
    with pytest.raises(services.AgentError):
        services.agent_decide(e1.pk, "apply", "Ops agent")  # already decided
    with pytest.raises(services.AgentError):
        services.agent_decide(e2.pk, "apply", "Ops agent")  # one late reply per wait
    services.agent_decide(e2.pk, "dismiss", "Ops agent")
    app.refresh_from_db()
    assert app.advance_count == 1


def test_extra_reply_after_resume_cannot_be_applied(waiting):
    app, wait, path, t0 = waiting
    call(path, "A", at=t0 + seconds(1))
    call(path, "B", at=t0 + seconds(2))
    with pytest.raises(services.AgentError):
        services.agent_decide(CallbackEvent.objects.get(idempotency_key="B").pk, "apply", "Ops agent")


def test_http_endpoint_checks_the_raw_body_signature(client, waiting):
    app, wait, path, t0 = waiting
    body, headers = partner_sim.build({"data": {"decision": "approved"}}, "http-1")
    hdrs = {"HTTP_IDEMPOTENCY_KEY": headers["Idempotency-Key"], "HTTP_X_LEDGER_TIMESTAMP": headers["X-Ledger-Timestamp"],
            "HTTP_X_LEDGER_SIGNATURE": headers["X-Ledger-Signature"]}
    r = client.post(path, data=body, content_type="application/json", **hdrs)
    assert r.status_code == 200 and r.json()["status"] == "resumed"
    # Same headers, body changed by one byte: signature no longer matches.
    r2 = client.post(path, data=body.replace(b"approved", b"approveD"), content_type="application/json", **hdrs)
    assert r2.status_code == 401
    assert json.loads(r2.content)["reason"] == "BAD_SIGNATURE"


def test_celery_timeout_task_runs_the_timeout_path(waiting):
    from ledger.tasks import expire_wait_task, sweep_expired_waits

    app, wait, path, t0 = waiting
    WaitStep.objects.filter(pk=wait.pk).update(timeout_at=timezone.now() - seconds(1))
    assert expire_wait_task.apply(args=[str(wait.pk)]).get() is True
    assert expire_wait_task.apply(args=[str(wait.pk)]).get() is False  # second delivery is a no-op
    assert sweep_expired_waits.apply().get() == 0
    app.refresh_from_db()
    assert app.status == Application.Status.TIMEOUT_PATH


def test_demo_api_end_to_end(client):
    """Reset, seed, open an application through the demo API, and read it back."""
    r = client.post("/api/demo/reset")
    assert r.status_code == 200 and r.json()["applications"] == 3
    r = client.post("/api/demo/applications", {"max_wait_seconds": 30}, content_type="application/json")
    ref = r.json()["ref"]
    assert r.json()["status"] == "WAITING"
    detail = client.get(f"/api/applications/{ref}").json()
    assert detail["wait"]["state"] == "WAITING" and detail["advance_count"] == 0
    parked = client.get("/api/parked").json()
    assert any(p["agent_status"] == "OPEN" for p in parked)  # the seeded late reply
