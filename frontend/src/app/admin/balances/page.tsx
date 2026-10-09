"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { ErrorState, LevelPill, PageHeader, Section, fmtMoney, fmtNum, humanize } from "@/components/admin/admin-ui";
import { adminApi, type BalanceCard, type ServiceSetting } from "@/lib/admin-api";

function money(v: number | null | undefined, currency?: string | null) {
  return fmtMoney(v ?? null, currency ?? "INR");
}

function BalanceTile({ card }: { card: BalanceCard }) {
  const level = card.status === "ok" ? card.level ?? "unknown" : card.status;
  const share =
    card.kind === "prepaid"
      ? card.remaining_share
      : card.limit && card.balance != null
        ? card.balance / card.limit
        : card.limit && card.used != null
          ? 1 - card.used / card.limit
          : null;

  let headline = "—";
  let sub: string | null = null;
  if (card.kind === "prepaid" && card.status === "ok") {
    headline = money(card.balance, card.currency);
    sub = `of ${money(card.prepaid_amount, card.currency)} · ${money(card.spent_since, card.currency)} used since entered`;
  } else if (card.kind === "postpaid") {
    headline = money(card.used, card.currency);
    sub = card.limit ? `this month of ${money(card.limit, card.currency)} budget` : "this month (no budget set)";
  } else if (card.kind === "quota") {
    headline = fmtNum(card.balance);
    sub = `left · ${fmtNum(card.used)} ${card.unit ?? ""}`;
  } else if (card.status === "ok") {
    if (card.balance != null) headline = card.currency ? money(card.balance, card.currency) : `${fmtNum(card.balance, 2)} ${card.unit ?? ""}`;
    else if (card.used != null) headline = `${fmtNum(card.used, 2)} ${card.unit ?? ""}`;
    if (card.limit != null) sub = `limit ${fmtNum(card.limit, 2)}${card.used != null && card.balance != null ? ` · ${fmtNum(card.used, 2)} used` : ""}`;
  }

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-medium">{card.label}</p>
            <p className="text-micro text-muted-foreground">{card.kind === "live" ? "live from provider" : card.kind}</p>
          </div>
          <LevelPill level={level} />
        </div>
        <p className="text-2xl font-semibold tabular-nums">{headline}</p>
        {share != null && (
          <div className="h-1.5 rounded-full bg-muted">
            <div
              className="h-1.5 rounded-full"
              style={{
                width: `${Math.max(0, Math.min(1, share)) * 100}%`,
                backgroundColor: level === "critical" ? "var(--destructive)" : level === "low" ? "var(--status-pending)" : "var(--status-live)",
              }}
            />
          </div>
        )}
        {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        {card.kind === "prepaid" && card.status === "ok" && (
          <p className="text-xs text-muted-foreground">
            Burning {money(card.burn_per_day, card.currency)}/day
            {card.days_left != null && ` · ~${fmtNum(card.days_left)} days left`}
          </p>
        )}
        {card.detail && <p className="text-xs text-muted-foreground">{card.detail}</p>}
      </CardContent>
    </Card>
  );
}

function SettingRow({ setting, onSaved }: { setting: ServiceSetting; onSaved: (rows: ServiceSetting[]) => void }) {
  const [prepaid, setPrepaid] = useState("");
  const [prices, setPrices] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setPrices(Object.fromEntries(Object.entries(setting.unit_prices).map(([k, v]) => [k, String(v)])));
  }, [setting]);

  const showPrepaid = ["sarvam", "exotel", "groq"].includes(setting.account);
  const priceKeys = Array.from(new Set([...Object.keys(setting.default_unit_prices), ...Object.keys(setting.unit_prices)]));
  if (!showPrepaid && priceKeys.length === 0) return null;

  async function save() {
    setSaving(true);
    try {
      const unit_prices = Object.fromEntries(
        Object.entries(prices)
          .filter(([, v]) => v.trim() !== "")
          .map(([k, v]) => [k, Number(v)])
      );
      if (Object.values(unit_prices).some((v) => Number.isNaN(v) || v < 0)) throw new Error("Prices must be non-negative numbers");
      const body: Parameters<typeof adminApi.settings.update>[1] = { unit_prices };
      if (prepaid.trim() !== "") body.prepaid_amount = Number(prepaid);
      const rows = await adminApi.settings.update(setting.account, body);
      setPrepaid("");
      onSaved(rows);
      toast.success(`${setting.label} saved`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const symbol = setting.currency === "USD" ? "$" : "₹";
  return (
    <div className="grid gap-4 border-t py-4 first:border-t-0 first:pt-0 md:grid-cols-[14rem_1fr_auto] md:items-end">
      <div>
        <p className="text-sm font-medium">{setting.label}</p>
        <p className="text-xs text-muted-foreground">
          {setting.prepaid_amount != null && setting.prepaid_set_at
            ? `${setting.account === "groq" ? "Budget" : "Balance"} ${symbol}${fmtNum(setting.prepaid_amount, 2)} entered ${new Date(setting.prepaid_set_at).toLocaleDateString("en-IN")}`
            : setting.kind === "fx"
              ? "Used to show USD costs in INR"
              : "Prices in " + setting.currency}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {showPrepaid && (
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground" htmlFor={`${setting.account}-prepaid`}>
              {setting.account === "groq" ? `Monthly budget (${symbol})` : `Current balance (${symbol})`}
            </Label>
            <Input
              id={`${setting.account}-prepaid`}
              inputMode="decimal"
              placeholder={setting.prepaid_amount != null ? String(setting.prepaid_amount) : "Enter amount"}
              value={prepaid}
              onChange={(e) => setPrepaid(e.target.value)}
            />
          </div>
        )}
        {priceKeys.map((key) => (
          <div key={key} className="space-y-1">
            <Label className="text-xs text-muted-foreground" htmlFor={`${setting.account}-${key}`}>
              {setting.kind === "fx" ? "₹ per $1" : `${symbol} per ${humanize(key).toLowerCase()}`}
            </Label>
            <Input
              id={`${setting.account}-${key}`}
              inputMode="decimal"
              value={prices[key] ?? ""}
              placeholder={String(setting.default_unit_prices[key] ?? "")}
              onChange={(e) => setPrices((p) => ({ ...p, [key]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      <Button size="sm" onClick={save} disabled={saving}>
        <Save data-icon="inline-start" />
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}

export default function AdminBalancesPage() {
  const [refreshing, setRefreshing] = useState(false);
  const balances = useAsync(() => adminApi.balances(), []);
  const settings = useAsync(() => adminApi.settings.list(), []);

  async function refresh() {
    setRefreshing(true);
    try {
      balances.setData(await adminApi.balances(true));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader title="Balances" subtitle="What's left in every account — recharge before anything runs dry" />
        <Button variant="outline" size="sm" onClick={refresh} disabled={refreshing}>
          <RefreshCw data-icon="inline-start" className={refreshing ? "animate-spin" : undefined} />
          Refresh live balances
        </Button>
      </div>
      {balances.error && <ErrorState error={balances.error} />}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {balances.loading && !balances.data
          ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-xl" />)
          : balances.data?.map((card) => <BalanceTile key={card.account} card={card} />)}
      </div>

      <Section
        title="Balances & prices"
        description="Sarvam and Exotel have no balance API: enter what your dashboard shows now, and Mira subtracts metered spend from that moment on. Re-enter it after every recharge. Default prices are estimates — match them to your invoices."
      >
        {settings.error && <ErrorState error={settings.error} />}
        {settings.data?.map((s) => (
          <SettingRow
            key={s.account}
            setting={s}
            onSaved={(rows) => {
              settings.setData(rows);
              balances.refetch();
            }}
          />
        ))}
      </Section>
    </>
  );
}
