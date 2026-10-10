"""Record the walkthrough against the live demo with headless Playwright.
Captions are drawn as a page overlay. Output: a .webm in video-work/ (convert with ffmpeg).
Usage: python scripts/record_video.py https://callback-ledger.onrender.com"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
OUT = "video-work"

CAPTION_JS = """
(text) => {
  let el = document.getElementById('cap');
  if (!el) {
    el = document.createElement('div'); el.id = 'cap';
    el.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:22px;max-width:1120px;' +
      'background:rgba(15,23,42,.93);color:#fff;font:600 25px/1.35 system-ui,sans-serif;padding:14px 26px;' +
      'border-radius:12px;z-index:9999;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.25)';
    document.body.appendChild(el);
  }
  el.textContent = text; el.style.display = text ? 'block' : 'none';
}
"""

RING_JS = """
(sel) => {
  document.querySelectorAll('.ring').forEach(e => { e.classList.remove('ring'); e.style.outline=''; });
  if (!sel) return;
  const e = document.querySelector(sel); if (!e) return;
  e.classList.add('ring'); e.style.outline = '4px solid #f79009'; e.style.outlineOffset = '3px';
}
"""

END_CARD = """
<html><body style="margin:0;height:720px;display:flex;align-items:center;justify-content:center;
background:#0f172a;color:#fff;font-family:system-ui,sans-serif;text-align:center">
<div><div style="font-size:52px;font-weight:700">Callback Ledger</div>
<div style="font-size:28px;margin-top:14px;color:#cbd5e1">Each paused application resumes exactly once.<br>
Late replies wait for a person. Forged calls are refused and logged.</div>
<div style="font-size:22px;margin-top:26px;color:#94a3b8">Django · Celery · Postgres · React · 20 pytest tests</div>
<div style="font-size:18px;margin-top:22px;color:#64748b">Synthetic data and a simulated partner. A reference pattern for a
"Wait for webhook" step, not LendAPI code.</div></div></body></html>
"""


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def main():
    post("/api/demo/reset")
    first = post("/api/demo/applications", {"max_wait_seconds": 300, "borrower": "Maya Ortiz",
                                             "dealer": "Harbor Auto (synthetic)"})
    marks = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                  record_video_dir=OUT, record_video_size={"width": 1280, "height": 720})
        page = ctx.new_page()
        t_start = time.time()
        page.goto(BASE + "/")
        page.wait_for_selector("text=your dealer is reviewing")
        page.locator(".chip", has_text=first["ref"]).click()
        page.wait_for_timeout(600)
        marks["start"] = time.time() - t_start
        cap = lambda s: page.evaluate(CAPTION_JS, s)
        ring = lambda s: page.evaluate(RING_JS, s)

        # 0-5 s: hook
        cap("Maya is stuck on the dealer tablet wait screen until the dealer's system calls back.")
        page.wait_for_timeout(2200)
        cap("The dealer's system retries the same reply 10 times at once...")
        page.get_by_role("button", name="Retry storm: same reply 10x").click()
        page.wait_for_selector(".last .code")
        page.wait_for_timeout(700)
        ring(".stat.hot")
        cap("10 callbacks in. Maya's file advanced once. She sees one offer.")
        page.wait_for_timeout(3600)

        # Story: why it matters
        ring("")
        cap("LendAPI v3.2.9 (Oct 6) added a \"Wait for webhook\" step: a flow pauses until a partner calls back.")
        page.wait_for_timeout(4200)
        cap("The hard cases are retries, late replies and forged calls. Those are what leave an ops agent fixing files by hand.")
        page.wait_for_timeout(4800)
        ring(".last")
        cap("Every retry with the same Idempotency-Key gets the original answer back (↺). Nothing moves twice.")
        page.wait_for_timeout(4200)
        ring(".timeline")
        cap("The timeline shows each retry, answered and not applied.")
        page.wait_for_timeout(3200)

        # Timeout path + late reply
        ring("")
        page.fill("input[type=number]", "6")
        cap("Next borrower. This time the dealer's system is slow. Max wait: 6 seconds.")
        page.get_by_role("button", name="New application").click()
        page.wait_for_selector("text=your dealer is reviewing")
        page.wait_for_selector("text=This is taking longer than usual", timeout=30000)
        cap("No reply in time. A Celery timer sends the flow down its timeout path, so the borrower is not left waiting.")
        page.wait_for_timeout(4200)
        cap("Then the dealer's reply arrives, late.")
        page.get_by_role("button", name="Late reply (after max wait)").click()
        page.wait_for_selector(".last .code")
        page.wait_for_timeout(500)
        ring(".queue")
        cap("It is parked for an ops agent (202), not applied on its own. The file has not moved.")
        page.wait_for_timeout(4400)
        cap("The agent checks it and clicks Apply. The file advances once, and the decision is logged.")
        page.wait_for_timeout(1500)
        page.locator(".parked").first.get_by_role("button", name="Apply").click()
        page.wait_for_selector("text=Your offer is ready")
        ring(".stat.hot")
        page.wait_for_timeout(3600)

        # Forged
        ring("")
        cap("Someone sends a callback signed with the wrong secret...")
        page.get_by_role("button", name="Forged callback").click()
        page.wait_for_selector(".rej")
        page.wait_for_timeout(500)
        ring(".queue:nth-of-type(2)")
        cap("HMAC signature check fails: 401, written to the audit log, file unchanged.")
        page.wait_for_timeout(4400)
        ring("")
        cap("")

        page.set_content(END_CARD)
        page.wait_for_timeout(5000)
        marks["end"] = time.time() - t_start
        video_path = page.video.path()
        ctx.close()
        browser.close()
    print(json.dumps({"video": video_path, **marks}))


if __name__ == "__main__":
    main()
