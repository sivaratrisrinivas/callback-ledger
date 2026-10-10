"""Drive the running app the way the video does: retry storm, late reply, forged call.
Usage: python scripts/partner_demo.py [base_url]   (default http://127.0.0.1:8000)"""
import sys
import time

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def post(path, body):
    r = requests.post(BASE + path, json=body, timeout=60)
    return r.json()


def show(app):
    print(f"  {app['ref']} {app['borrower_name']}: {app['status']} / {app['stage']} | callbacks {app['callbacks_received']}, "
          f"advanced {app['advance_count']}x, parked {app['parked_open']}, rejected {app['rejected']}")


print("1. Partner retries the same reply 10 times at once")
app = post("/api/demo/applications", {"max_wait_seconds": 30})
out = post(f"/api/demo/partner/{app['ref']}", {"scenario": "retry-storm", "n": 10})
print("  partner got:", " ".join(f"{r['http']}{'(replayed)' if r['replayed'] else ''}" for r in out["results"]))
show(out["application"])

print("2. Partner replies after the max wait (5 s)")
app = post("/api/demo/applications", {"max_wait_seconds": 5})
time.sleep(7)
out = post(f"/api/demo/partner/{app['ref']}", {"scenario": "late-reply"})
print("  partner got:", out["results"][0]["http"], out["results"][0]["body"].get("status"))
show(out["application"])

print("3. Forged callback (wrong signing secret)")
out = post(f"/api/demo/partner/{app['ref']}", {"scenario": "forged"})
print("  partner got:", out["results"][0]["http"], out["results"][0]["body"].get("reason"))
show(out["application"])
