import os

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import demo, partner_sim, services
from .models import Application, CallbackEvent, RejectedCallback, WaitStep


def _iso(dt):
    return dt.isoformat() if dt else None


def wait_json(w: WaitStep):
    return {
        "id": str(w.id), "step_name": w.step_name, "state": w.state, "max_wait_seconds": w.max_wait_seconds,
        "timeout_at": _iso(w.timeout_at), "link_expires_at": _iso(w.link_expires_at),
        "resolved_at": _iso(w.resolved_at), "resolved_by": w.resolved_by, "result": w.result,
    }


def event_json(e: CallbackEvent):
    return {
        "id": e.id, "application": e.wait.application.ref, "borrower": e.wait.application.borrower_name,
        "idempotency_key": e.idempotency_key, "payload": e.payload, "received_at": _iso(e.received_at),
        "outcome": e.outcome, "response_status": e.response_status, "retry_count": e.retry_count,
        "agent_status": e.agent_status, "agent_name": e.agent_name, "agent_note": e.agent_note,
        "wait_state": e.wait.state, "timeout_at": _iso(e.wait.timeout_at),
    }


def reject_json(r: RejectedCallback):
    return {
        "id": r.id, "application": r.wait.application.ref if r.wait else None, "reason": r.reason,
        "reason_label": RejectedCallback.Reason(r.reason).label, "status_code": r.status_code,
        "received_at": _iso(r.received_at),
    }


def app_summary(a: Application):
    events = CallbackEvent.objects.filter(wait__application=a)
    return {
        "ref": a.ref, "borrower_name": a.borrower_name, "dealer_name": a.dealer_name, "product": a.product,
        "amount": str(a.amount), "status": a.status, "stage": a.stage, "advance_count": a.advance_count,
        "callbacks_received": sum(1 + e.retry_count for e in events),
        "retries_absorbed": sum(e.retry_count for e in events),
        "parked_open": sum(1 for e in events if e.agent_status == "OPEN"),
        "rejected": RejectedCallback.objects.filter(wait__application=a).count(),
        "wait": wait_json(w) if (w := demo.latest_wait(a)) else None,
        "created_at": _iso(a.created_at),
    }


# ------------------------------------------------------------- partner-facing endpoint

@api_view(["POST"])
def callback(request, token):
    body = request.body  # raw bytes, read before DRF parses anything; the signature covers them
    headers = {k: request.headers.get(k) for k in ("X-Ledger-Timestamp", "X-Ledger-Signature", "Idempotency-Key")}
    reply = services.handle_callback(token, body, headers, request.META.get("REMOTE_ADDR", ""))
    return Response(reply.body, status=reply.status, headers=reply.headers)


# ------------------------------------------------------------- flow + agent API

@api_view(["POST"])
def open_wait(request, ref):
    app = get_object_or_404(Application, ref=ref)
    max_wait = int(request.data.get("max_wait_seconds", 60))
    if not 1 <= max_wait <= 7 * 24 * 3600:
        return Response({"error": "max_wait_seconds out of range"}, status=400)
    wait, path = services.open_wait(app, request.data.get("step_name", demo.STEP)[:120], max_wait)
    return Response({"wait": wait_json(wait), "callback_url": settings.PUBLIC_BASE_URL.rstrip("/") + path}, status=201)


@api_view(["GET"])
def applications(request):
    return Response([app_summary(a) for a in Application.objects.all()[:50]])


@api_view(["GET"])
def application_detail(request, ref):
    a = get_object_or_404(Application, ref=ref)
    data = app_summary(a)
    data["timeline"] = [{"at": _iso(t.at), "kind": t.kind, "message": t.message} for t in a.timeline.all()]
    data["events"] = [event_json(e) for e in CallbackEvent.objects.filter(wait__application=a).select_related("wait__application")]
    return Response(data)


@api_view(["GET"])
def parked(request):
    qs = CallbackEvent.objects.filter(agent_status__in=["OPEN", "APPLIED", "DISMISSED"]).select_related("wait__application").order_by("-received_at")[:50]
    return Response([event_json(e) for e in qs])


@api_view(["POST"])
def decide(request, event_id):
    try:
        e = services.agent_decide(event_id, request.data.get("action", ""), str(request.data.get("agent", "Ops agent"))[:80],
                                  str(request.data.get("note", ""))[:280])
    except CallbackEvent.DoesNotExist:
        return Response({"error": "not found"}, status=404)
    except services.AgentError as exc:
        return Response({"error": str(exc)}, status=409)
    return Response(event_json(e))


@api_view(["GET"])
def rejected(request):
    return Response([reject_json(r) for r in RejectedCallback.objects.select_related("wait__application")[:50]])


# ------------------------------------------------------------- demo-only (simulated partner)

def _sim_base():
    return settings.SIMULATOR_TARGET or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


@api_view(["POST"])
def demo_new_application(request):
    max_wait = max(5, min(int(request.data.get("max_wait_seconds", 30)), 600))
    app, _wait, _path = demo.new_application(max_wait, request.data.get("borrower"), request.data.get("dealer"))
    return Response(app_summary(Application.objects.get(pk=app.pk)), status=201)


@api_view(["POST"])
def demo_partner(request, ref):
    app = get_object_or_404(Application, ref=ref)
    wait = demo.latest_wait(app)
    if wait is None or not hasattr(wait, "partner_copy"):
        return Response({"error": "no simulated partner link for this application"}, status=404)
    path, base = wait.partner_copy.callback_path, _sim_base()
    scenario = request.data.get("scenario")
    if scenario == "retry-storm":
        n = max(2, min(int(request.data.get("n", 10)), 20))
        key, results = partner_sim.retry_storm(base, path, n, request.data.get("decision", "approved"))
    elif scenario == "late-reply":
        if wait.state == WaitStep.State.WAITING and timezone.now() < wait.timeout_at:
            return Response({"error": "the wait is still open; a late reply needs the maximum wait to pass"}, status=409)
        key, r = partner_sim.single(base, path, request.data.get("decision", "approved"))
        results = [r]
    elif scenario == "forged":
        key, r = partner_sim.forged(base, path)
        results = [r]
    else:
        return Response({"error": "scenario must be retry-storm, late-reply or forged"}, status=400)
    app.refresh_from_db()
    return Response({"scenario": scenario, "idempotency_key": key, "results": results, "application": app_summary(app)})


@api_view(["POST"])
def demo_reset(request):
    demo.reset()
    return Response({"ok": True})


def health(request):
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return JsonResponse({"ok": True, "db": connection.vendor, "celery": settings.CELERY_ENABLED})
