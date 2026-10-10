# Callback Ledger

Maya is at a car dealer, on the dealer's tablet. Her loan application is paused until the dealer's review system calls back, and she sits on the wait screen. If that system retries its reply, her file must not move twice. If it answers after the maximum wait, the flow has already taken its timeout path, and an ops agent ends up fixing the file by hand the next morning.

This service makes each paused application resume **exactly once**, **parks late replies** for an agent instead of advancing the file, and **refuses forged callbacks** (HMAC signature check). Django, Celery, Postgres, with a thin React agent view.

**Live demo:** https://callback-ledger.onrender.com (free tier, first load can take a minute) · **Video (61 s, silent):** https://callback-ledger.onrender.com/demo.mp4 ([repo copy](frontend/public/demo.mp4))

**Why this shape.** LendAPI v3.2.9 ([Oct 6, 2026 release notes](https://www.lendapi.com/blog/flow-can-now-wait-for-the-outside-world)) added "Send webhook" and "Wait for webhook" steps to Flow Builder: the flow "pauses and holds its place until the other system calls back" on a unique callback link, and after a maximum wait time "the flow takes a separate path". This repo is a reference pattern for the backend edge of that step, in the stack the [role](https://www.lendapi.com/careers/front-end-and-back-end-engineer) lists (Python, Django, Celery, React). It is not LendAPI code and says nothing about how LendAPI handles callbacks today.

## Run it (about 3 minutes, no keys, no Docker)

```bash
make setup   # Python 3.11+ and Node 20: venv, deps, React build, SQLite, 3 synthetic applications
make run     # http://127.0.0.1:8000 (web plus the timeout sweeper)
make demo    # second terminal: retry storm, late reply, forged call
make test    # 20 tests; DATABASE_URL=postgres://... make test runs them on Postgres
```
Full target stack (Postgres, Redis, Celery worker and beat): `docker compose up`, then http://localhost:8000. I could not run Docker on my build machine, so the compose file is untested; the same stack (Postgres 17, Redis, Celery worker with beat, gunicorn) was run and tested directly.

## How it works ([ledger/services.py](ledger/services.py))

```
WAITING --reply before max wait--> RESUMED        file advances once
WAITING --max wait passes--------> TIMED_OUT      timeout path (Celery ETA task, beat sweeper as backup)
TIMED_OUT --late reply--> parked --agent Apply--> AGENT_APPLIED   file advances once, logged
RESUMED --reply with a new key--> parked (dismiss only)
```
- Callback link `/api/callbacks/<wait id>.<expiry>.<HMAC>`; only its SHA-256 hash is stored. Tampered link: 401. Past expiry: 410.
- The partner signs `timestamp.body` with HMAC-SHA256 (`X-Ledger-Signature`), 5 minute window, constant-time compare. Bad or stale: 401 plus an audit row.
- `Idempotency-Key` is required and unique per wait (DB constraint). A retry gets the stored answer back (`Idempotent-Replayed: true`). Same key, different body: 422.
- The callback and the timeout both lock the wait row (`SELECT ... FOR UPDATE`) in one transaction. Whoever takes the lock first wins; the other is recorded, never applied. A reply at or after the deadline is late.

## Tests ([tests/test_callbacks.py](tests/test_callbacks.py), plain pytest)
10 concurrent duplicates resume once (in-process threads and over real HTTP) · reply after timeout is parked · late reply before the timer fired still takes the timeout path · reply at the exact deadline is late · timeout and callback racing 15 times · forged signature · tampered link · replayed old signature · expired link · out-of-order keys · same key with a new body · missing key · sweeper and Celery task are idempotent · agent can apply one late reply per wait · demo API. All 20 pass on SQLite and on Postgres 17.

## Real vs simulated
- **Real:** the API, state machine, signatures, idempotency, row locks, Celery timeouts, agent queue, audit log, React view.
- **Simulated:** the partner system ([ledger/partner_sim.py](ledger/partner_sim.py)) and the "Simulated partner" buttons that drive it, all applicants and dealers (invented names), the flow (one wait step, no real Flow Builder), and the agent (no login).
- **Demo hosting:** Render free web service with **embedded SQLite** (the account's one free Render Postgres slot is in use), Celery broker on a free Render Key Value instance, worker and beat in the same container. Data resets on restart. Postgres is the target.
- **No load numbers.** I did not run a load test, so I make no throughput claims.

## Next, with real data
Real retry and late rates per partner, the timeouts tenants pick, per-tenant signing keys, and where dedupe lives today. Related idea: with parallel steps (v3.2.2), a small simulator could show the cost of paid data pulls for applicants a cheap check would have declined.

Not built: auth and tenancy, a real Flow Builder, real partner integrations, load tests. Synthetic data only. Not affiliated with LendAPI.
