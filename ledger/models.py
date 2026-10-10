import uuid

from django.db import models


class Application(models.Model):
    """A loan application moving through a flow. Synthetic data only."""

    class Status(models.TextChoices):
        IN_FLOW = "IN_FLOW", "In flow"
        WAITING = "WAITING", "Waiting for webhook"
        RESUMED = "RESUMED", "Resumed"
        TIMEOUT_PATH = "TIMEOUT_PATH", "On timeout path"
        AGENT_RESOLVED = "AGENT_RESOLVED", "Resolved by agent"

    ref = models.CharField(max_length=32, unique=True)
    borrower_name = models.CharField(max_length=80)
    dealer_name = models.CharField(max_length=80)
    product = models.CharField(max_length=40, default="Auto loan")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_FLOW)
    stage = models.CharField(max_length=120, default="Application submitted")
    # How many times the flow moved past a wait. The whole point: this must never exceed
    # the number of waits the application went through.
    advance_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.ref


class WaitStep(models.Model):
    """One 'Wait for webhook' step: the flow is paused until a partner calls back."""

    class State(models.TextChoices):
        WAITING = "WAITING", "Waiting"
        RESUMED = "RESUMED", "Resumed by callback"
        TIMED_OUT = "TIMED_OUT", "Timed out (timeout path)"
        AGENT_APPLIED = "AGENT_APPLIED", "Late reply applied by agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="waits")
    step_name = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, unique=True)
    max_wait_seconds = models.PositiveIntegerField()
    timeout_at = models.DateTimeField(db_index=True)
    link_expires_at = models.DateTimeField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.WAITING, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=80, blank=True)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CallbackEvent(models.Model):
    """A distinct partner reply (one per idempotency key). Retries bump retry_count."""

    class Outcome(models.TextChoices):
        APPLIED = "APPLIED", "Resumed the flow"
        LATE_PARKED = "LATE_PARKED", "Late: parked for an agent"
        EXTRA_PARKED = "EXTRA_PARKED", "Extra reply after resume: parked"

    class AgentStatus(models.TextChoices):
        NONE = "NONE", "Not parked"
        OPEN = "OPEN", "Waiting for an agent"
        APPLIED = "APPLIED", "Applied by agent"
        DISMISSED = "DISMISSED", "Dismissed by agent"

    wait = models.ForeignKey(WaitStep, on_delete=models.CASCADE, related_name="events")
    idempotency_key = models.CharField(max_length=128)
    payload = models.JSONField()
    body_sha256 = models.CharField(max_length=64)
    received_at = models.DateTimeField()
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    retry_count = models.PositiveIntegerField(default=0)
    agent_status = models.CharField(max_length=12, choices=AgentStatus.choices, default=AgentStatus.NONE)
    agent_name = models.CharField(max_length=80, blank=True)
    agent_note = models.CharField(max_length=280, blank=True)
    agent_decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["received_at"]
        constraints = [
            models.UniqueConstraint(fields=["wait", "idempotency_key"], name="one_event_per_idempotency_key")
        ]


class RejectedCallback(models.Model):
    """Audit row for every callback we refused (forged, expired, malformed)."""

    class Reason(models.TextChoices):
        BAD_LINK = "BAD_LINK", "Callback link not valid"
        BAD_SIGNATURE = "BAD_SIGNATURE", "Signature does not match"
        STALE_TIMESTAMP = "STALE_TIMESTAMP", "Signature timestamp too old or missing"
        LINK_EXPIRED = "LINK_EXPIRED", "Callback link expired"
        MISSING_KEY = "MISSING_KEY", "Idempotency-Key header missing"
        KEY_REUSED = "KEY_REUSED", "Idempotency key reused with a different body"
        BAD_JSON = "BAD_JSON", "Body is not valid JSON"

    wait = models.ForeignKey(WaitStep, null=True, blank=True, on_delete=models.CASCADE, related_name="rejections")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    status_code = models.PositiveSmallIntegerField()
    received_at = models.DateTimeField()
    remote_addr = models.CharField(max_length=64, blank=True)
    detail = models.CharField(max_length=280, blank=True)

    class Meta:
        ordering = ["-received_at"]


class TimelineEntry(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="timeline")
    at = models.DateTimeField()
    kind = models.CharField(max_length=32)
    message = models.CharField(max_length=280)

    class Meta:
        ordering = ["at", "id"]


class SimulatedPartnerLink(models.Model):
    """DEMO ONLY: the simulated partner's own copy of the callback link it was handed.
    A real partner keeps this on its side; the ledger itself only stores a hash."""

    wait = models.OneToOneField(WaitStep, on_delete=models.CASCADE, related_name="partner_copy")
    callback_path = models.CharField(max_length=300)
