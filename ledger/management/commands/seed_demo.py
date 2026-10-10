"""Seed a few synthetic applications through the real ledger code (no HTTP).
Runs on boot of the demo when the database is empty."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger import demo, partner_sim, services
from ledger.models import Application, SimulatedPartnerLink


def _call(path, key, decision, at):
    body, headers = partner_sim.build({"data": {"decision": decision, "reviewer": "Dealer desk (simulated)"}}, key,
                                      timestamp=int(at.timestamp()))
    return services.handle_callback(path.rsplit("/", 1)[1], body, headers, "seed", received_at=at)


class Command(BaseCommand):
    help = "Seed synthetic demo applications (only if the database is empty, unless --force)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, force=False, **opts):
        if Application.objects.exists() and not force:
            self.stdout.write("already seeded")
            return
        if force:
            demo.reset()
        now = timezone.now()

        def opened(borrower, dealer, minutes_ago, max_wait):
            app = Application.objects.create(ref=f"APP-{Application.objects.count() + 1038}", borrower_name=borrower,
                                             dealer_name=dealer, amount=24800)
            t0 = now - timedelta(minutes=minutes_ago)
            services.note(app, "submitted", "Application submitted on the dealer tablet.", t0)
            wait, path = services.open_wait(app, demo.STEP, max_wait, now=t0)
            SimulatedPartnerLink.objects.create(wait=wait, callback_path=path)
            return app, wait, path, t0

        # 1. Partner retried four times; resumed once.
        _a, _w, path, t0 = opened("Lena Fischer", "Northgate Motors (synthetic)", 50, 120)
        for _ in range(4):
            _call(path, "dealer-review-7f3a", "approved", t0 + timedelta(seconds=40))
        # 2. No reply in time; the late reply is waiting for an agent.
        _a, w, path, t0 = opened("Omar Haddad", "Ridgeview Marine (synthetic)", 30, 120)
        services.expire_wait(w.pk, now=t0 + timedelta(seconds=121))
        _call(path, "dealer-review-91c2", "approved", t0 + timedelta(minutes=9))
        # 3. Someone tried a forged callback; refused and logged. Then the real reply arrived.
        _a, _w, path, t0 = opened("Ana Lima", "Harbor Auto (synthetic)", 12, 120)
        body, headers = partner_sim.build({"data": {"decision": "approved", "amount": 99000}}, "x-forged",
                                          secret="not-the-shared-secret", timestamp=int((t0 + timedelta(seconds=20)).timestamp()))
        services.handle_callback(path.rsplit("/", 1)[1], body, headers, "seed", received_at=t0 + timedelta(seconds=20))
        _call(path, "dealer-review-c410", "approved", t0 + timedelta(seconds=55))
        self.stdout.write(f"seeded {Application.objects.count()} applications")
