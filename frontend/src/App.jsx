import React, { useCallback, useEffect, useState } from "react";

const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
};

const STATUS_LABEL = {
  IN_FLOW: "In flow",
  WAITING: "Waiting for webhook",
  RESUMED: "Resumed",
  TIMEOUT_PATH: "Timeout path",
  AGENT_RESOLVED: "Resolved by agent",
};

const time = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "");

function useNow() {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  return now;
}

function Tablet({ app, now }) {
  if (!app) return <div className="tablet"><div className="screen empty">No application yet</div></div>;
  const first = app.borrower_name.split(" ")[0];
  const left = app.wait ? Math.max(0, Math.ceil((new Date(app.wait.timeout_at) - now) / 1000)) : 0;
  let body;
  if (app.status === "WAITING") {
    body = (
      <>
        <div className="spinner" />
        <h3>Hi {first}, your dealer is reviewing your application.</h3>
        <p>Please stay on this screen.</p>
        <p className="muted small">Flow paused on "Wait for webhook". Max wait left: {left}s</p>
      </>
    );
  } else if (app.status === "RESUMED" || app.status === "AGENT_RESOLVED") {
    const ok = app.stage.startsWith("Offer ready");
    body = ok ? (
      <>
        <div className="check">✓</div>
        <h3>Your offer is ready, {first}.</h3>
        <p className="big">${Number(app.amount).toLocaleString()}</p>
        <p className="muted">{app.product} · {app.dealer_name}</p>
      </>
    ) : (
      <>
        <h3>Thanks, {first}.</h3>
        <p>{app.stage}</p>
      </>
    );
  } else if (app.status === "TIMEOUT_PATH") {
    body = (
      <>
        <div className="clock">◷</div>
        <h3>Thanks, {first}. This is taking longer than usual.</h3>
        <p>A team member will follow up with you shortly.</p>
        <p className="muted small">Max wait passed. Flow took its timeout path.</p>
      </>
    );
  } else {
    body = <h3>{app.stage}</h3>;
  }
  return (
    <div className="tablet">
      <div className="tablet-bar">Dealer tablet · borrower view</div>
      <div className="screen">{body}</div>
    </div>
  );
}

function Stat({ label, value, hot }) {
  return (
    <div className={"stat" + (hot ? " hot" : "")}>
      <div className="v">{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}

export default function App() {
  const now = useNow();
  const [apps, setApps] = useState([]);
  const [sel, setSel] = useState(null);
  const [detail, setDetail] = useState(null);
  const [parked, setParked] = useState([]);
  const [rejected, setRejected] = useState([]);
  const [busy, setBusy] = useState("");
  const [last, setLast] = useState(null);
  const [maxWait, setMaxWait] = useState(20);

  const refresh = useCallback(async () => {
    const [a, p, r] = await Promise.all([api("/api/applications"), api("/api/parked"), api("/api/rejected")]);
    if (a.ok) {
      setApps(a.data);
      if (!sel && a.data.length) setSel(a.data[0].ref);
    }
    if (p.ok) setParked(p.data);
    if (r.ok) setRejected(r.data);
    if (sel) {
      const d = await api(`/api/applications/${sel}`);
      if (d.ok) setDetail(d.data);
    }
  }, [sel]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 1000);
    return () => clearInterval(t);
  }, [refresh]);

  const run = async (label, fn) => {
    setBusy(label);
    try {
      await fn();
    } finally {
      setBusy("");
      refresh();
    }
  };

  const newApp = () =>
    run("new", async () => {
      const r = await api("/api/demo/applications", { method: "POST", body: { max_wait_seconds: Number(maxWait) } });
      if (r.ok) {
        setApps((prev) => [r.data, ...prev]);
        setDetail({ ...r.data, timeline: [] });
        setSel(r.data.ref);
        setLast(null);
      }
    });

  const partner = (scenario) =>
    run(scenario, async () => {
      const r = await api(`/api/demo/partner/${sel}`, { method: "POST", body: { scenario, n: 10 } });
      setLast(r.ok ? r.data : { error: r.data.error || `HTTP ${r.status}` });
    });

  const decide = (id, action) =>
    run("decide", () => api(`/api/parked/${id}/decision`, { method: "POST", body: { action, agent: "Ops agent (you)" } }));

  const d = detail && detail.ref === sel ? detail : apps.find((x) => x.ref === sel);
  const waitOpen = d && d.status === "WAITING";
  const pastDeadline = d && d.wait && new Date(d.wait.timeout_at) <= now;
  const openParked = parked.filter((p) => p.agent_status === "OPEN");

  return (
    <div className="page">
      <header>
        <div>
          <h1>Callback Ledger</h1>
          <span className="sub">Exactly-once resume for a loan flow's "Wait for webhook" step</span>
        </div>
        <span className="note">Synthetic data · simulated partner · reference pattern, not LendAPI code</span>
      </header>

      <main>
        <section className="left">
          <Tablet app={d} now={now} />
          <div className="apps">
            <div className="row">
              <button onClick={newApp} disabled={!!busy}>New application</button>
              <label className="muted small">
                max wait{" "}
                <input type="number" min="5" max="600" value={maxWait} onChange={(e) => setMaxWait(e.target.value)} />s
              </label>
            </div>
            <div className="chips">
              {apps.slice(0, 8).map((a) => (
                <button key={a.ref} className={"chip " + (a.ref === sel ? "on" : "")} onClick={() => setSel(a.ref)}>
                  {a.ref} <span className={"dot s-" + a.status} />
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="mid">
          {d ? (
            <>
              <div className="card head">
                <div>
                  <b>{d.ref}</b> · {d.borrower_name} · {d.dealer_name}
                  <div className="muted small">{d.stage}</div>
                </div>
                <span className={"pill s-" + d.status}>{STATUS_LABEL[d.status]}</span>
              </div>
              <div className="stats">
                <Stat label="callbacks received" value={d.callbacks_received} />
                <Stat label="retries answered, not applied" value={d.retries_absorbed} />
                <Stat label="times the file advanced" value={d.advance_count} hot />
                <Stat label="parked for an agent" value={d.parked_open} />
                <Stat label="rejected" value={d.rejected} />
              </div>
              <div className="card">
                <div className="title">Simulated partner (dealer review system)</div>
                <div className="row">
                  <button onClick={() => partner("retry-storm")} disabled={!!busy}>
                    {busy === "retry-storm" ? "Sending..." : "Retry storm: same reply 10x"}
                  </button>
                  <button onClick={() => partner("late-reply")} disabled={!!busy || (waitOpen && !pastDeadline)}>
                    Late reply (after max wait)
                  </button>
                  <button className="warn" onClick={() => partner("forged")} disabled={!!busy}>
                    Forged callback
                  </button>
                </div>
                {last && (
                  <div className="last small">
                    {last.error ? (
                      <span className="err">{last.error}</span>
                    ) : (
                      <>
                        Partner got:{" "}
                        {last.results.map((r, i) => (
                          <span key={i} className={"code c" + String(r.http)[0]}>
                            {r.http}
                            {r.replayed ? "↺" : ""}
                          </span>
                        ))}
                        <span className="muted"> ↺ = original answer replayed</span>
                      </>
                    )}
                  </div>
                )}
              </div>
              <div className="card timeline">
                <div className="title">Application timeline</div>
                {(detail?.timeline || []).slice(-6).map((t, i) => (
                  <div key={i} className={"tl k-" + t.kind}>
                    <span className="muted">{time(t.at)}</span> {t.message}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="card">Create an application to start.</div>
          )}
        </section>

        <section className="right">
          <div className="card queue">
            <div className="title">
              Parked callbacks <span className="muted">(agent queue, {openParked.length} open)</span>
            </div>
            {parked.length === 0 && <div className="muted small">Nothing parked. Late or extra replies land here.</div>}
            {parked.slice(0, 5).map((p) => (
              <div key={p.id} className="parked">
                <div>
                  <b>{p.application}</b> · {p.borrower}
                  <div className="small muted">
                    {p.outcome === "LATE_PARKED" ? "Arrived after max wait" : "Arrived after resume"} · says "
                    {(p.payload.data || p.payload).decision}" · {time(p.received_at)}
                  </div>
                </div>
                {p.agent_status === "OPEN" ? (
                  <div className="row tight">
                    {p.outcome === "LATE_PARKED" && p.wait_state === "TIMED_OUT" && (
                      <button className="small-btn" onClick={() => decide(p.id, "apply")}>Apply</button>
                    )}
                    <button className="small-btn ghost" onClick={() => decide(p.id, "dismiss")}>Dismiss</button>
                  </div>
                ) : (
                  <span className="pill done">{p.agent_status.toLowerCase()}</span>
                )}
              </div>
            ))}
          </div>
          <div className="card queue">
            <div className="title">Rejected callbacks <span className="muted">(audit log)</span></div>
            {rejected.length === 0 && <div className="muted small">None yet.</div>}
            {rejected.slice(0, 4).map((r) => (
              <div key={r.id} className="rej small">
                <span className="code c4">{r.status_code}</span> {r.application || "unknown link"} · {r.reason_label} ·{" "}
                <span className="muted">{time(r.received_at)}</span>
              </div>
            ))}
          </div>
          <div className="card rules small">
            <div className="title">Rules</div>
            <div>1. A retry with the same Idempotency-Key gets the original answer. The file moves once.</div>
            <div>2. A reply after the max wait is parked for an agent, never applied on its own.</div>
            <div>3. A body that fails the HMAC signature check is refused (401) and logged.</div>
          </div>
        </section>
      </main>
    </div>
  );
}
