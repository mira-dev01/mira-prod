const BACKEND_BASE_URL = process.env.BACKEND_BASE_URL ?? "http://localhost:8000";

interface LlmModelHealth {
  ok?: boolean;
  latency_s?: number;
  checked_at?: string;
  error?: string | null;
  [key: string]: unknown;
}

async function getLlmHealth(): Promise<Record<string, LlmModelHealth> | null> {
  try {
    const res = await fetch(`${BACKEND_BASE_URL}/api/v1/health/llm`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json();
    return body.models ?? {};
  } catch {
    return null;
  }
}

// Static reference rates -- MIRA has no per-call cost metering wired up yet
// (see docs/architecture.md / project_state.md), so this is "what the
// provider bills per unit," not "what we spent this month." Treat as a
// planning number, re-check against each provider's own pricing page before
// using it for anything financial.
const COST_REFERENCE: { service: string; whatItsFor: string; pricingNote: string }[] = [
  {
    service: "Groq (LLM)",
    whatItsFor: "Voice agent's function-calling model (gpt-oss-120b primary, see docs/agents.md)",
    pricingNote: "Free tier has a per-model tokens/min cap -- the fallback chain exists specifically to route around it, not to control spend. Confirm current paid-tier $/token on console.groq.com before scaling call volume.",
  },
  {
    service: "Sarvam AI (STT + TTS)",
    whatItsFor: "Speech-to-text and bulbul:v3 text-to-speech for every call turn",
    pricingNote: "Billed per audio second/character depending on plan -- check the Sarvam dashboard for the account's actual tier.",
  },
  {
    service: "Exotel (telephony)",
    whatItsFor: "Inbound call routing, exophone numbers, the raw-PCM media stream",
    pricingNote: "Per-minute call + monthly exophone rental. Check the Exotel billing dashboard directly -- rates vary by number type and route.",
  },
  {
    service: "Bright Data",
    whatItsFor: "Airbnb listing scrape on property import (dataset gd_ld7ll037kqy322v05)",
    pricingNote: "Billed per scrape/record via Bright Data's Web Scraper API. Only fires on host-triggered imports, not per call.",
  },
  {
    service: "TURN relay",
    whatItsFor: "Browser \"talk to Mira\" WebRTC test calls on networks that block UDP",
    pricingNote: "Only used by the in-dashboard voice test feature, not real guest calls (those go through Exotel). Check your TURN provider's bandwidth-based billing.",
  },
  {
    service: "SMTP",
    whatItsFor: "Escalation emails to hosts",
    pricingNote: "Free if using an existing Gmail/Workspace/Zoho inbox; check quota if using a dedicated provider (SES, etc).",
  },
];

export default async function FounderDashboard() {
  const models = await getLlmHealth();

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "40px 24px" }}>
      <h1 style={{ color: "#d94f3d", marginBottom: 4 }}>Founder Console</h1>
      <p style={{ color: "#635747", marginTop: 0, fontSize: 14 }}>
        Internal API health & cost reference. Deliberately not linked from the host dashboard.
      </p>

      <section style={{ marginTop: 32 }}>
        <h2 style={{ fontSize: 16 }}>LLM model health (live)</h2>
        <p style={{ fontSize: 13, color: "#635747" }}>
          From the backend&apos;s <code>GET /api/v1/health/llm</code> -- the same data
          <code> _pick_groq_model()</code> uses to route around a rate-limited model mid-call.
        </p>
        {!models ? (
          <p style={{ color: "#d94f3d" }}>
            Could not reach the backend at {BACKEND_BASE_URL}. Is it running?
          </p>
        ) : Object.keys(models).length === 0 ? (
          <p style={{ color: "#635747" }}>No health data yet -- the first check runs at backend startup.</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #e8e0d5" }}>
                <th style={{ padding: "6px 8px" }}>Model</th>
                <th style={{ padding: "6px 8px" }}>Status</th>
                <th style={{ padding: "6px 8px" }}>Latency</th>
                <th style={{ padding: "6px 8px" }}>Last checked</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(models).map(([name, info]) => (
                <tr key={name} style={{ borderBottom: "1px solid #e8e0d5" }}>
                  <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{name}</td>
                  <td style={{ padding: "6px 8px", color: info.ok ? "#7aaf6e" : "#d94f3d" }}>
                    {info.ok ? "up" : `down${info.error ? `: ${info.error}` : ""}`}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {info.latency_s != null ? `${Math.round(info.latency_s * 1000)} ms` : "-"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{String(info.checked_at ?? "-")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section style={{ marginTop: 40 }}>
        <h2 style={{ fontSize: 16 }}>External API cost reference (estimated, not metered)</h2>
        <p style={{ fontSize: 13, color: "#635747" }}>
          No provider bills into this app yet. Real spend tracking needs each provider&apos;s
          own billing/usage API wired up per-service -- this table is a planning reference,
          not a live number.
        </p>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #e8e0d5" }}>
              <th style={{ padding: "6px 8px" }}>Service</th>
              <th style={{ padding: "6px 8px" }}>Used for</th>
              <th style={{ padding: "6px 8px" }}>Pricing note</th>
            </tr>
          </thead>
          <tbody>
            {COST_REFERENCE.map((row) => (
              <tr key={row.service} style={{ borderBottom: "1px solid #e8e0d5", verticalAlign: "top" }}>
                <td style={{ padding: "6px 8px", fontWeight: 600, whiteSpace: "nowrap" }}>{row.service}</td>
                <td style={{ padding: "6px 8px" }}>{row.whatItsFor}</td>
                <td style={{ padding: "6px 8px", color: "#635747" }}>{row.pricingNote}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
