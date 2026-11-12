"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { OptionCard } from "@/components/option-card";
import { FieldRow, Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
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
        />
        <OptionCard
          checked={value.kind === "split"}
          onCheckedChange={(on) => on && update({ kind: "split" })}
          title="An advance, then the rest"
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
        <span className="text-muted-foreground">minutes while the guest pays.</span>
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
          <p className="text-[13px] text-muted-foreground">
            {own ? `Own rules: ${describe({ ...portfolio, ...override })}` : "Follows the shared rules"}
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
  const [activeWhen, setActiveWhen] = useState(config.booking.active_when ?? "confirmed");
  const [payment, setPayment] = useState<Payment>(config.payment);
  const [saving, setSaving] = useState(false);
  const billing = useAsync(() => api.billing.get(), []);
  const properties = useAsync(() => api.properties.list(), []);

  const valid = inRange(String(payment.split_percent), LIMITS.percent) && inRange(String(payment.hold_minutes), LIMITS.hold);
  const dirty =
    confirmBy !== config.booking.confirm_by ||
    activeWhen !== (config.booking.active_when ?? "confirmed") ||
    JSON.stringify(payment) !== JSON.stringify(config.payment);
  const connected = billing.data?.razorpay.connected ?? false;

  async function save() {
    setSaving(true);
    try {
      const result = await api.agentConfig.update({ booking: { confirm_by: confirmBy, active_when: activeWhen }, payment });
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
      <Sheet>
        <WideCell>
          <SheetHeading title="Confirming and paying for a booking" />

          {billing.loading && !billing.data ? (
            <div className="col-span-12 border-b border-border p-5">
              <Skeleton className="h-10 w-full" />
            </div>
          ) : !connected ? (
            <p className="col-span-12 border-b border-border p-5 text-sm">
              Mira can only send payment links once Razorpay is connected.{" "}
              <Link href="/dashboard/settings?tab=billing" className="font-semibold underline underline-offset-2 hover:no-underline">
                Connect it in Settings → Billing
              </Link>
              . Until then, you confirm every booking yourself.
            </p>
          ) : null}

          <FieldRow label="Who confirms a booking?">
            <div>
              <OptionCard
                checked={confirmBy === "host"}
                onCheckedChange={(on) => on && setConfirmBy("host")}
                title="I do"
                description="You get a ticket to confirm once a payment arrives."
              />
              <OptionCard
                checked={confirmBy === "payment"}
                onCheckedChange={(on) => on && setConfirmBy("payment")}
                title="A verified payment does"
                description="Mira checks the payment with Razorpay and confirms. You're told straight away."
              />
            </div>
          </FieldRow>

          <FieldRow label="Show as an active booking">
            <div>
              <OptionCard
                checked={activeWhen === "confirmed"}
                onCheckedChange={(on) => on && setActiveWhen("confirmed")}
                title="When it is confirmed"
              />
              <OptionCard
                checked={activeWhen === "paid"}
                onCheckedChange={(on) => on && setActiveWhen("paid")}
                title="When the guest has paid"
              />
              <OptionCard
                checked={activeWhen === "link_sent"}
                onCheckedChange={(on) => on && setActiveWhen("link_sent")}
                title="When the payment link is sent"
                description="The guest is listed in Active bookings and the request is closed in Booking requests."
              />
            </div>
          </FieldRow>

          <FieldRow label="What does the guest pay?">
            <PaymentFields value={payment} onChange={setPayment} />
          </FieldRow>

          <SheetFooter>
            <Button variant="cta" onClick={save} disabled={!dirty || saving || !valid}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </SheetFooter>
        </WideCell>
      </Sheet>

      {(properties.data?.length ?? 0) > 1 && (
        <Sheet>
          <WideCell>
            <SheetHeading title="By property" />
            <div className="col-span-12 px-5 py-2">
              <p className="py-2 text-[13px] text-muted-foreground">
                <span className="mr-1 inline-block size-2 rounded-full align-middle" style={{ backgroundColor: "var(--status-live)" }} />
                follows the shared rules
                <span className="mx-1 ml-3 inline-block size-2 rounded-full align-middle" style={{ backgroundColor: "var(--status-pending)" }} />
                has its own
              </p>
              <ul className="divide-y divide-border">
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
          </WideCell>
        </Sheet>
      )}
    </div>
  );
}
