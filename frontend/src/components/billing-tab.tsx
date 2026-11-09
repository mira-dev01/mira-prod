"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Cell, Hint, Sheet, SheetRow, WideCell } from "@/components/settings/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { BillingTemplateName, BillingView, PaymentRequestOut } from "@/lib/types";

const SAMPLE: Record<string, string> = {
  guest_name: "Asha",
  property: "Sea View Villa",
  check_in: "12 Dec 2026",
  check_out: "15 Dec 2026",
  amount: "₹12,000",
  total: "₹40,000",
  balance_note: "The remaining ₹28,000 is due by 5 Dec 2026.",
  link: "https://rzp.io/i/AbC123",
  hold_minutes: "60",
  host_name: "Your name",
};

// Same replacement rule as the server: only {name} tokens, nothing evaluated.
function preview(text: string): string {
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => SAMPLE[name] ?? whole).trim();
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function Razorpay({ billing, onChange }: { billing: BillingView; onChange: (next: BillingView) => void }) {
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const { razorpay } = billing;

  async function connect() {
    setBusy(true);
    try {
      onChange(await api.billing.connectRazorpay(keyId.trim(), secret.trim()));
      setKeyId("");
      setSecret("");
      toast.success("Razorpay connected");
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't connect Razorpay"));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      onChange(await api.billing.disconnectRazorpay());
      toast.success("Razorpay disconnected");
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't disconnect"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SheetRow title="Razorpay" hint="Guests pay straight into your own Razorpay account. Mira never holds the money.">
      {!billing.encryption_ready ? (
        <p className="text-sm" style={{ color: "var(--status-pending-strong)" }}>
          Online payment isn&apos;t switched on for this server yet. Ask support to finish setting it up.
        </p>
      ) : razorpay.connected ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: "var(--status-live)" }} />
            <span className="font-semibold">Connected</span>
            <span className="text-muted-foreground">{razorpay.key_id}</span>
            <span className="rounded-sm border border-foreground/30 px-2 py-0.5 text-xs font-semibold">
              {razorpay.mode === "test" ? "Test mode" : "Live"}
            </span>
          </div>
          {razorpay.mode === "test" && (
            <p className="text-sm" style={{ color: "var(--status-pending-strong)" }}>
              These are test keys, so no real money moves. Connect your live keys to take real bookings.
            </p>
          )}
          <Button variant="outline" size="sm" className="self-start" onClick={disconnect} disabled={busy}>
            Disconnect
          </Button>
        </>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rzp-key">Key ID</Label>
              <Input id="rzp-key" value={keyId} onChange={(e) => setKeyId(e.target.value)} placeholder="rzp_live_…" autoComplete="off" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rzp-secret">Key Secret</Label>
              <Input
                id="rzp-secret"
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                autoComplete="new-password"
              />
            </div>
          </div>
          <Hint>
            Find them in Razorpay → Account &amp; Settings → API Keys. We check them with Razorpay, then store the secret encrypted. It is never shown again.
          </Hint>
          <Button className="self-start" onClick={connect} disabled={busy || !keyId.trim() || !secret.trim()}>
            {busy ? "Checking…" : "Connect"}
          </Button>
        </>
      )}
    </SheetRow>
  );
}

function MessageCard({ name, billing, onChange }: { name: BillingTemplateName; billing: BillingView; onChange: (next: BillingView) => void }) {
  const template = billing.templates[name];
  const [text, setText] = useState(template.draft ?? template.text);
  const [busy, setBusy] = useState(false);
  const dirty = text.trim() !== (template.draft ?? template.text).trim();
  const pending = template.draft !== null && !dirty;

  async function run(action: () => Promise<BillingView>, done: string) {
    setBusy(true);
    try {
      const next = await action();
      onChange(next);
      setText(next.templates[name].draft ?? next.templates[name].text);
      toast.success(done);
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't save"));
    } finally {
      setBusy(false);
    }
  }

  const status = pending ? "Edited, not live yet" : template.is_default ? "Standard wording" : "Your wording";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold">{template.label}</p>
        <span className="text-[13px] text-muted-foreground">{status}</span>
      </div>
      <Textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={900} aria-label={template.label} className="min-h-24" />
      <div className="flex flex-wrap gap-1.5">
        {billing.placeholders.map((p) => (
          <button
            key={p.name}
            type="button"
            title={p.help}
            onClick={() => setText((t) => `${t}${t.endsWith(" ") || t === "" ? "" : " "}{${p.name}}`)}
            className="rounded-sm border border-border px-2 py-0.5 font-mono text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {`{${p.name}}`}
          </button>
        ))}
      </div>
      <div className="rounded-md bg-secondary px-3 py-2 text-sm whitespace-pre-wrap">{preview(text)}</div>
      {pending && (
        <p className="text-[13px]" style={{ color: "var(--status-pending-strong)" }}>
          Guests still get the previous wording until you approve this.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {pending ? (
          <Button size="sm" onClick={() => run(() => api.billing.approveTemplate(name), "Approved. Guests will get this.")} disabled={busy}>
            Approve &amp; use
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={() => run(() => api.billing.saveTemplate(name, text), "Saved. Approve it to use it.")} disabled={busy || !dirty || !text.trim()}>
            Save
          </Button>
        )}
        {!template.is_default && (
          <Button size="sm" variant="ghost" onClick={() => run(() => api.billing.resetTemplate(name), "Back to the standard wording")} disabled={busy}>
            Use standard wording
          </Button>
        )}
      </div>
    </div>
  );
}

function PaymentList({ rows, onChanged }: { rows: PaymentRequestOut[]; onChanged: () => void }) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function release(id: string) {
    setBusyId(id);
    try {
      await api.payments.cancel(id);
      toast.success("Dates released");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't release"));
    } finally {
      setBusyId(null);
    }
  }

  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No payment links yet.</p>;
  return (
    <ul className="divide-y">
      {rows.slice(0, 15).map((r) => (
        <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
          <div className="min-w-0">
            <p className="font-semibold">
              {r.guest_name ?? r.guest_phone ?? "Guest"} · {r.property_name}
            </p>
            <p className="text-[13px] text-muted-foreground">
              {r.check_in} → {r.check_out} · ₹{Number(r.amount_due).toLocaleString("en-IN")}
              {r.kind === "advance" ? " advance" : ""}
              {r.status === "active" ? ` · held until ${new Date(r.hold_expires_at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] capitalize text-muted-foreground">{r.status === "active" ? "Waiting for payment" : r.status}</span>
            {r.status === "active" && (
              <Button size="sm" variant="ghost" disabled={busyId === r.id} onClick={() => release(r.id)}>
                Release dates
              </Button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function BillingTab() {
  const { data, setData, loading, error } = useAsync(() => api.billing.get(), []);
  const payments = useAsync(() => api.payments.list(), []);
  const [savingNotify, setSavingNotify] = useState(false);

  if (loading && !data) {
    return (
      <Sheet>
        <Cell>
          <Skeleton variant="text" className="w-1/3" />
          <Skeleton variant="text" />
          <Skeleton variant="text" className="w-2/3" />
        </Cell>
      </Sheet>
    );
  }
  if (!data) {
    return (
      <Sheet>
        <Cell className="md:col-span-6">
          <p className="text-sm text-destructive">{error?.message ?? "Could not load billing."}</p>
        </Cell>
      </Sheet>
    );
  }
  const billing = data;

  async function toggleNotify(checked: boolean) {
    setSavingNotify(true);
    try {
      setData(await api.billing.setNotifyHost(checked));
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't save"));
    } finally {
      setSavingNotify(false);
    }
  }

  return (
    <Sheet>
      {billing.razorpay.connected && !billing.capability_enabled && (
        <Cell>
          <p className="text-sm">
            Online payments are switched off, so Mira won&apos;t send payment links.{" "}
            <Link href="/dashboard/settings?tab=features" className="font-semibold text-primary underline underline-offset-2">
              Turn them on in Settings → Features
            </Link>
            .
          </p>
        </Cell>
      )}

      <WideCell>
        <Razorpay billing={billing} onChange={setData} />

        {billing.razorpay.connected && (
          <>
            <SheetRow title="Messages guests get" hint="Edit the wording, then approve it. Guests only ever get approved wording.">
              <div className="grid gap-6 lg:grid-cols-2">
                <MessageCard name="payment_request" billing={billing} onChange={setData} />
                <MessageCard name="payment_success" billing={billing} onChange={setData} />
              </div>
            </SheetRow>

            <SheetRow title="Payment alerts">
              <label className="flex items-start gap-3 text-sm">
                <Checkbox
                  checked={billing.notify_host_on_payment}
                  disabled={savingNotify}
                  onCheckedChange={(checked) => toggleNotify(checked === true)}
                />
                <span>
                  <span className="font-semibold">WhatsApp me when a guest pays</span>
                  <span className="block text-[13px] text-muted-foreground">You always get an alert in the app.</span>
                </span>
              </label>
            </SheetRow>

            <SheetRow title="Payment links" hint="Dates are held for a guest until they pay or the hold runs out.">
              {payments.data ? <PaymentList rows={payments.data} onChanged={payments.refetch} /> : <Skeleton className="h-16 w-full" />}
            </SheetRow>
          </>
        )}
      </WideCell>
    </Sheet>
  );
}
