"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { OptionCard } from "@/components/option-card";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { AgentConfig } from "@/lib/types";

type Payment = AgentConfig["payment"];
type Overrides = Record<string, Partial<Payment>>;

const LIMITS = { percent: [1, 99], days: [0, 90], hold: [5, 1440] } as const;

function inRange(value: string, [min, max]: readonly [number, number]): boolean {
  const n = Number(value);
  return value.trim() !== "" && Number.isInteger(n) && n >= min && n <= max;
}

function describe(p: Payment): string {
  const how = p.kind === "split" ? `${p.split_percent}% now, rest ${p.balance_due_days_before_checkin} days before check-in` : "Full amount";
  return `${how} · dates held ${p.hold_minutes} min`;
}

/** The amount and hold fields, shared by the portfolio rules and a property's own. */
function PaymentFields({ value, onChange }: { value: Payment; onChange: (next: Payment) => void }) {
  const [percent, setPercent] = useState(String(value.split_percent));
  const [days, setDays] = useState(String(value.balance_due_days_before_checkin));
  const [hold, setHold] = useState(String(value.hold_minutes));

  function update(patch: Partial<Payment>, texts: { percent?: string; days?: string; hold?: string } = {}) {
    const p = texts.percent ?? percent;
    const d = texts.days ?? days;
    const h = texts.hold ?? hold;
    onChange({
      ...value,
      ...patch,
      split_percent: inRange(p, LIMITS.percent) ? Number(p) : value.split_percent,
      balance_due_days_before_checkin: inRange(d, LIMITS.days) ? Number(d) : value.balance_due_days_before_checkin,
      hold_minutes: inRange(h, LIMITS.hold) ? Number(h) : value.hold_minutes,
    });
  }

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <OptionCard
          checked={value.kind === "full"}
          onCheckedChange={(on) => on && update({ kind: "full" })}
          title="The full amount"
          description="The guest pays the whole stay price to book."
        />
        <OptionCard
          checked={value.kind === "split"}
          onCheckedChange={(on) => on && update({ kind: "split" })}
          title="An advance, then the rest"
          description="The guest pays part now; the link tells them when the balance is due."
        >
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Input
              type="number"
              min={1}
              max={99}
              value={percent}
              onChange={(e) => {
                setPercent(e.target.value);
                update({}, { percent: e.target.value });
              }}
              className="w-20"
              aria-label="Advance percent"
              aria-invalid={!inRange(percent, LIMITS.percent)}
            />
            % now, the rest
            <Input
              type="number"
              min={0}
              max={90}
              value={days}
              onChange={(e) => {
                setDays(e.target.value);
                update({}, { days: e.target.value });
              }}
              className="w-20"
              aria-label="Days before check-in"
              aria-invalid={!inRange(days, LIMITS.days)}
            />
            days before check-in
          </div>
        </OptionCard>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Label htmlFor="hold-minutes" className="font-normal text-muted-foreground">
          Hold the dates for
        </Label>
        <Input
          id="hold-minutes"
          type="number"
          min={5}
          max={1440}
          value={hold}
          onChange={(e) => {
            setHold(e.target.value);
            update({}, { hold: e.target.value });
          }}
          className="w-24"
          aria-invalid={!inRange(hold, LIMITS.hold)}
        />
        <span className="text-muted-foreground">minutes while the guest pays. No one else can book them meanwhile.</span>
      </div>
    </div>
  );
}

function PropertyRule({
  property,
  portfolio,
  override,
  onChanged,
}: {
  property: { id: string; name: string };
  portfolio: Payment;
  override: Partial<Payment> | undefined;
  onChanged: (overrides: Overrides) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Payment>({ ...portfolio, ...override });
  const [busy, setBusy] = useState(false);
  const valid = inRange(String(draft.split_percent), LIMITS.percent) && inRange(String(draft.hold_minutes), LIMITS.hold);

  async function run(action: () => Promise<{ payment_overrides: Overrides }>, done: string) {
    setBusy(true);
    try {
      onChanged((await action()).payment_overrides);
      setEditing(false);
      toast.success(done);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  const own = override && Object.keys(override).length > 0;
  return (
    <li className="space-y-2 py-3">
      <div className="flex items-start gap-2">
        <span
          aria-hidden
          className="mt-1.5 inline-block size-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: own ? "var(--status-pending)" : "var(--status-live)" }}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{property.name}</p>
          <p className="text-xs text-muted-foreground">
            {own ? `Own rules: ${describe({ ...portfolio, ...override })}` : "Follows the rules above"}
          </p>
        </div>
        {!editing && (
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            {own ? "Edit" : "Customise"}
          </Button>
        )}
      </div>
      {editing && (
        <div className="space-y-3 pl-[18px]">
          <PaymentFields value={draft} onChange={setDraft} />
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={busy || !valid} onClick={() => run(() => api.agentConfig.setPaymentOverride(property.id, draft), "Saved")}>
              Save
            </Button>
            {own && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => api.agentConfig.clearPaymentOverride(property.id), "Back to the shared rules")}>
                Use the shared rules
              </Button>
            )}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

/** How a booking is confirmed and paid for, for the whole portfolio and per property. */
export function PaymentsStep({
  config,
  overrides,
  onSaved,
  onOverridesChanged,
}: {
  config: AgentConfig;
  overrides: Overrides;
  onSaved: (next: AgentConfig) => void;
  onOverridesChanged: (next: Overrides) => void;
}) {
  const [confirmBy, setConfirmBy] = useState(config.booking.confirm_by);
  const [payment, setPayment] = useState<Payment>(config.payment);
  const [saving, setSaving] = useState(false);
  const billing = useAsync(() => api.billing.get(), []);
  const properties = useAsync(() => api.properties.list(), []);

  const valid = inRange(String(payment.split_percent), LIMITS.percent) && inRange(String(payment.hold_minutes), LIMITS.hold);
  const dirty =
    confirmBy !== config.booking.confirm_by || JSON.stringify(payment) !== JSON.stringify(config.payment);
  const connected = billing.data?.razorpay.connected ?? false;

  async function save() {
    setSaving(true);
    try {
      const result = await api.agentConfig.update({ booking: { confirm_by: confirmBy }, payment });
      onSaved(result.config);
      toast.success("Saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-medium">Confirming and paying for a booking</h2>
        <p className="text-sm text-muted-foreground">What happens once a guest agrees a price.</p>
      </div>

      {billing.loading && !billing.data ? (
        <Skeleton className="h-10 w-full" />
      ) : !connected ? (
        <p className="rounded-lg border px-3 py-2 text-sm">
          Mira can only send payment links once Razorpay is connected.{" "}
          <Link href="/dashboard/settings?tab=billing" className="font-medium underline underline-offset-2">
            Connect it in Settings → Billing
          </Link>
          . Until then, you confirm every booking yourself.
        </p>
      ) : null}

      <div className="space-y-2">
        <p className="text-sm font-medium">Who confirms a booking?</p>
        <OptionCard
          checked={confirmBy === "host"}
          onCheckedChange={(on) => on && setConfirmBy("host")}
          title="I do"
          description="A payment is received, then you get a ticket to confirm the booking."
        />
        <OptionCard
          checked={confirmBy === "payment"}
          onCheckedChange={(on) => on && setConfirmBy("payment")}
          title="A verified payment does"
          description="Mira checks the payment with Razorpay and marks the booking confirmed. You're told straight away."
        />
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">What does the guest pay?</p>
        <PaymentFields value={payment} onChange={setPayment} />
      </div>

      <Button onClick={save} disabled={!dirty || saving || !valid}>
        {saving ? "Saving…" : "Save"}
      </Button>

      {(properties.data?.length ?? 0) > 1 && (
        <div className="space-y-1">
          <div>
            <p className="text-sm font-medium">By property</p>
            <p className="text-xs text-muted-foreground">
              <span className="mr-1 inline-block size-2 rounded-full align-middle" style={{ backgroundColor: "var(--status-live)" }} />
              follows the rules above
              <span className="mx-1 ml-3 inline-block size-2 rounded-full align-middle" style={{ backgroundColor: "var(--status-pending)" }} />
              has its own
            </p>
          </div>
          <ul className="divide-y">
            {properties.data!.map((p) => (
              <PropertyRule
                key={p.id}
                property={{ id: p.id, name: p.name }}
                portfolio={config.payment}
                override={overrides[p.id]}
                onChanged={onOverridesChanged}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
