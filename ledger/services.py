"""The ledger: open a wait, take a callback, apply the timeout path, let an agent decide.

Every state change happens inside one transaction that holds a row lock on the WaitStep
(SELECT ... FOR UPDATE on Postgres; on SQLite the IMMEDIATE transaction serializes writers).
The callback handler and the timeout path take the same lock, so whichever runs first wins
and the other one only records what happened.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import signing
from .models import Application, CallbackEvent, RejectedCallback, TimelineEntry, WaitStep

log = logging.getLogger("ledger")


@dataclass
class Reply:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


def note(app: Application, kind: str, message: str, at: datetime | None = None):
    TimelineEntry.objects.create(application=app, at=at or timezone.now(), kind=kind, message=message)


# ---------------------------------------------------------------- opening a wait

def open_wait(app: Application, step_name: str, max_wait_seconds: int, now: datetime | None = None,
              link_ttl_seconds: int | None = None):
    """Pause the application on a 'Wait for webhook' step. Returns (wait, callback_path).
    The raw link is returned once; only its hash is stored."""
    now = now or timezone.now()
    ttl = link_ttl_seconds if link_ttl_seconds is not None else settings.CALLBACK_LINK_TTL_SECONDS
    ttl = max(ttl, max_wait_seconds)
    link_expires_at = now + timedelta(seconds=ttl)
    with transaction.atomic():
        wait = WaitStep(
            application=app,
            step_name=step_name,
            max_wait_seconds=max_wait_seconds,
            timeout_at=now + timedelta(seconds=max_wait_seconds),
            link_expires_at=link_expires_at,
        )
        token = signing.make_link_token(wait.id, int(link_expires_at.timestamp()))
        wait.token_hash = signing.token_hash(token)
        wait.save()
        Application.objects.filter(pk=app.pk).update(status=Application.Status.WAITING, stage=f"Paused: {step_name}")
        note(app, "wait_opened", f"Flow paused on '{step_name}'. Maximum wait {max_wait_seconds}s.", now)
        transaction.on_commit(lambda: schedule_timeout(wait))
    return wait, f"/api/callbacks/{token}"


def schedule_timeout(wait: WaitStep):
    if not settings.CELERY_ENABLED:
        return  # the sweeper applies timeouts
    from .tasks import expire_wait_task

    try:
        expire_wait_task.apply_async(args=[str(wait.id)], eta=wait.timeout_at)
    except Exception:  # broker down: the beat sweeper is the safety net
        log.exception("could not schedule timeout for %s; sweeper will catch it", wait.id)


# ---------------------------------------------------------------- timeout path

def _apply_timeout(wait: WaitStep, now: datetime):
    """Caller must hold the row lock and have checked state == WAITING."""
    wait.state = WaitStep.State.TIMED_OUT
    wait.resolved_at = now
    wait.resolved_by = "timeout"
    wait.save(update_fields=["state", "resolved_at", "resolved_by"])
    app = wait.application
    Application.objects.filter(pk=app.pk).update(
        status=Application.Status.TIMEOUT_PATH,
        stage="Timeout path: sent to the manual review queue",
    )
    note(app, "timeout", f"No reply within {wait.max_wait_seconds}s. Flow took the timeout path.", now)


def expire_wait(wait_id, now: datetime | None = None) -> bool:
    """Run the timeout path if the wait is still open and past its maximum wait. Idempotent."""
    now = now or timezone.now()
    with transaction.atomic():
        wait = WaitStep.objects.select_for_update().select_related("application").filter(pk=wait_id).first()
        if wait is None or wait.state != WaitStep.State.WAITING or now < wait.timeout_at:
            return False
        _apply_timeout(wait, now)
        return True


def sweep_expired(now: datetime | None = None) -> int:
    now = now or timezone.now()
    ids = list(WaitStep.objects.filter(state=WaitStep.State.WAITING, timeout_at__lte=now).values_list("pk", flat=True))
    return sum(1 for wid in ids if expire_wait(wid, now))


# ---------------------------------------------------------------- callbacks

def _reject(reason, status, received_at, remote_addr, wait=None, detail="") -> Reply:
    RejectedCallback.objects.create(
        wait=wait, reason=reason, status_code=status, received_at=received_at,
        remote_addr=remote_addr[:64], detail=detail[:280],
    )
    if wait is not None:
        note(wait.application, "rejected", f"Refused a callback ({RejectedCallback.Reason(reason).label}). Logged, file not moved.", received_at)
    return Reply(status, {"status": "rejected", "reason": reason})


def handle_callback(token: str, body: bytes, headers: dict, remote_addr: str = "",
                    received_at: datetime | None = None) -> Reply:
    received_at = received_at or timezone.now()
    R = RejectedCallback.Reason

    parsed = signing.parse_link_token(token)
    if parsed is None:
        return _reject(R.BAD_LINK, 401, received_at, remote_addr, detail="link signature check failed")
    wait_id, _exp = parsed
    wait = WaitStep.objects.select_related("application").filter(pk=wait_id).first()
    if wait is None or not signing.hmac.compare_digest(wait.token_hash, signing.token_hash(token)):
        return _reject(R.BAD_LINK, 401, received_at, remote_addr, detail="no wait for this link")

    sig_problem = signing.check_body_signature(
        body, headers.get("X-Ledger-Timestamp"), headers.get("X-Ledger-Signature"), received_at.timestamp()
    )
    if sig_problem == "stale":
        return _reject(R.STALE_TIMESTAMP, 401, received_at, remote_addr, wait)
    if sig_problem == "bad":
        return _reject(R.BAD_SIGNATURE, 401, received_at, remote_addr, wait)

    if received_at >= wait.link_expires_at:
        return _reject(R.LINK_EXPIRED, 410, received_at, remote_addr, wait)

    key = (headers.get("Idempotency-Key") or "").strip()
    if not key or len(key) > 128:
        return _reject(R.MISSING_KEY, 400, received_at, remote_addr, wait)
    try:
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        return _reject(R.BAD_JSON, 400, received_at, remote_addr, wait)
    body_hash = hashlib.sha256(body).hexdigest()

    for _attempt in range(3):
        try:
            return _record_callback(wait_id, key, payload, body_hash, received_at, remote_addr)
        except IntegrityError:
            # Only reachable if two transactions inserted the same key without the lock
            # (it cannot happen while the lock is held). Retry: the next pass replays it.
            time.sleep(0.01)
    raise RuntimeError("could not record callback")


def _record_callback(wait_id, key, payload, body_hash, received_at, remote_addr) -> Reply:
    R = RejectedCallback.Reason
    with transaction.atomic():
        wait = WaitStep.objects.select_for_update().select_related("application").get(pk=wait_id)
        app = wait.application

        existing = CallbackEvent.objects.filter(wait=wait, idempotency_key=key).first()
        if existing is not None:
            if existing.body_sha256 != body_hash:
                return _reject(R.KEY_REUSED, 422, received_at, remote_addr, wait,
                               detail=f"key {key} first seen with a different body")
            CallbackEvent.objects.filter(pk=existing.pk).update(retry_count=F("retry_count") + 1)
            note(app, "retry", f"Retry of {key}: answered with the original result. File not moved.", received_at)
            return Reply(existing.response_status, existing.response_body, {"Idempotent-Replayed": "true"})

        if wait.state == WaitStep.State.WAITING and received_at >= wait.timeout_at:
            # The reply is late and the timeout task has not run yet. Run the timeout path
            # now, under the same lock, so the flow behaves the same either way.
            _apply_timeout(wait, received_at)

        data = payload.get("data", payload)
        if wait.state == WaitStep.State.WAITING:
            outcome, agent_status = CallbackEvent.Outcome.APPLIED, CallbackEvent.AgentStatus.NONE
            status, resp = 200, {"status": "resumed", "application": app.ref, "idempotency_key": key}
            decision = str(data.get("decision", "approved")).lower()
            wait.state = WaitStep.State.RESUMED
            wait.resolved_at = received_at
            wait.resolved_by = f"callback {key}"
            wait.result = data
            wait.save(update_fields=["state", "resolved_at", "resolved_by", "result"])
            Application.objects.filter(pk=app.pk).update(
                status=Application.Status.RESUMED,
                stage="Offer ready" if decision == "approved" else f"Dealer review: {decision}",
                advance_count=F("advance_count") + 1,
            )
            note(app, "resumed", f"Callback {key} arrived in time. Flow resumed ({decision}).", received_at)
        elif wait.state in (WaitStep.State.TIMED_OUT,):
            outcome, agent_status = CallbackEvent.Outcome.LATE_PARKED, CallbackEvent.AgentStatus.OPEN
            status, resp = 202, {"status": "parked", "reason": "arrived after the maximum wait; an agent will review it",
                                 "application": app.ref, "idempotency_key": key}
            note(app, "parked", f"Late reply {key} arrived after the timeout path ran. Parked for an agent.", received_at)
        else:
            outcome, agent_status = CallbackEvent.Outcome.EXTRA_PARKED, CallbackEvent.AgentStatus.OPEN
            status, resp = 202, {"status": "parked", "reason": "this wait was already resolved; an agent will review it",
                                 "application": app.ref, "idempotency_key": key}
            note(app, "parked", f"Another reply ({key}) after the wait was resolved. Parked for an agent.", received_at)

        with transaction.atomic():  # savepoint so a unique clash cannot poison the outer transaction
            CallbackEvent.objects.create(
                wait=wait, idempotency_key=key, payload=payload, body_sha256=body_hash, received_at=received_at,
                outcome=outcome, response_status=status, response_body=resp, agent_status=agent_status,
            )
        return Reply(status, resp)


# ---------------------------------------------------------------- agent decisions

class AgentError(Exception):
    pass


def agent_decide(event_id: int, action: str, agent_name: str, note_text: str = "", now: datetime | None = None):
    """An agent applies or dismisses a parked reply. Only a late reply on a timed-out wait can be
    applied, and only one per wait."""
    now = now or timezone.now()
    if action not in ("apply", "dismiss"):
        raise AgentError("action must be apply or dismiss")
    with transaction.atomic():
        wait_id = CallbackEvent.objects.values_list("wait_id", flat=True).get(pk=event_id)
        wait = WaitStep.objects.select_for_update().select_related("application").get(pk=wait_id)
        event = CallbackEvent.objects.get(pk=event_id)
        app = wait.application
        if event.agent_status != CallbackEvent.AgentStatus.OPEN:
            raise AgentError("already decided")
        if action == "apply":
            if wait.state != WaitStep.State.TIMED_OUT:
                raise AgentError("only a late reply on a timed-out wait can be applied")
            data = event.payload.get("data", event.payload)
            decision = str(data.get("decision", "approved")).lower()
            wait.state = WaitStep.State.AGENT_APPLIED
            wait.resolved_by = f"agent {agent_name}"
            wait.result = data
            wait.save(update_fields=["state", "resolved_by", "result"])
            Application.objects.filter(pk=app.pk).update(
                status=Application.Status.AGENT_RESOLVED,
                stage="Offer ready (late reply applied by agent)" if decision == "approved" else f"Dealer review: {decision} (agent)",
                advance_count=F("advance_count") + 1,
            )
            event.agent_status = CallbackEvent.AgentStatus.APPLIED
            note(app, "agent_applied", f"{agent_name} applied late reply {event.idempotency_key}. {note_text}".strip(), now)
        else:
            event.agent_status = CallbackEvent.AgentStatus.DISMISSED
            note(app, "agent_dismissed", f"{agent_name} dismissed reply {event.idempotency_key}. {note_text}".strip(), now)
        event.agent_name, event.agent_note, event.agent_decided_at = agent_name[:80], note_text[:280], now
        event.save(update_fields=["agent_status", "agent_name", "agent_note", "agent_decided_at"])
        return event
