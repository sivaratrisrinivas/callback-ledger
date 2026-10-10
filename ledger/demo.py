"""Synthetic demo data. Names are invented; no real people, dealers or lenders."""
import random
from decimal import Decimal

from django.db import transaction

from . import services
from .models import Application, RejectedCallback, SimulatedPartnerLink, WaitStep

BORROWERS = ["Maya Ortiz", "Dev Raman", "Lena Fischer", "Omar Haddad", "Priya Nair", "Sam Okafor",
             "Ana Lima", "Jon Park", "Rosa Delgado", "Ken Ito", "Asha Menon", "Leo Brandt"]
DEALERS = ["Harbor Auto (synthetic)", "Ridgeview Marine (synthetic)", "Northgate Motors (synthetic)"]
STEP = "Wait for webhook: dealer review"


def new_application(max_wait_seconds=30, borrower=None, dealer=None):
    n = Application.objects.count() + 1041
    app = Application.objects.create(
        ref=f"APP-{n}",
        borrower_name=borrower or random.choice(BORROWERS),
        dealer_name=dealer or random.choice(DEALERS),
        product="Auto loan",
        amount=Decimal(random.choice([18500, 22900, 27400, 31250])),
    )
    services.note(app, "submitted", "Application submitted on the dealer tablet.")
    wait, path = services.open_wait(app, STEP, max_wait_seconds)
    SimulatedPartnerLink.objects.create(wait=wait, callback_path=path)
    return app, wait, path


@transaction.atomic
def reset():
    RejectedCallback.objects.all().delete()
    Application.objects.all().delete()


def latest_wait(app):
    return WaitStep.objects.filter(application=app).order_by("-created_at").first()
