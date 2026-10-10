import time

from django.core.management.base import BaseCommand

from ledger import services


class Command(BaseCommand):
    help = "Apply the timeout path to waits past their maximum wait. --loop to keep running (no-broker mode)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--every", type=float, default=2.0)

    def handle(self, *args, loop=False, every=2.0, **opts):
        while True:
            n = services.sweep_expired()
            if n:
                self.stdout.write(f"timeout path applied to {n} wait(s)")
            if not loop:
                return
            time.sleep(every)
