"use client";

import Link from "next/link";
import { ArrowRight, Clock, IndianRupee, MessageCircleQuestion, Phone, PhoneCall, ShieldAlert, Users, Zap } from "lucide-react";
import { StatCard } from "@/components/stat-card";
import { useAdminQuery } from "@/components/admin/admin-context";
import {
  CoverageNote,
  DecisionList,
  ErrorState,
  Funnel,
  Histogram,
  LoadingGrid,
  PageHeader,
  Section,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtSec,
} from "@/components/admin/admin-ui";
import { adminApi } from "@/lib/admin-api";

export default function AdminOverviewPage() {
  const { data, loading, error } = useAdminQuery(adminApi.overview);

  return (
    <>
      <PageHeader title="Overview" subtitle="Across every host and property" />
      {error && <ErrorState error={error} />}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
        <StatCard icon={Phone} label="Calls" value={fmtNum(data?.kpis.calls)} loading={loading} />
        <StatCard icon={PhoneCall} iconColorVar="--status-live" label="Answered" value={fmtNum(data?.kpis.answered)} loading={loading} />
        <StatCard icon={Zap} label="Engaged" value={fmtPct(data?.kpis.engaged_rate)} loading={loading} />
        <StatCard icon={Users} label="Leads" value={fmtNum(data?.kpis.leads)} loading={loading} />
        <StatCard icon={ShieldAlert} iconColorVar="--destructive" label="Escalated" value={fmtNum(data?.kpis.escalated)} loading={loading} />
        <StatCard icon={Clock} iconColorVar="--status-progress" label="Response p50" value={fmtSec(data?.kpis.response_p50_s)} loading={loading} />
        <StatCard
          icon={MessageCircleQuestion}
          iconColorVar="--status-pending"
          label="Phantom barge-ins"
          value={fmtPct(data?.kpis.phantom_rate)}
          loading={loading}
        />
        <StatCard icon={IndianRupee} label="Cost / answered call" value={fmtMoney(data?.kpis.cost_per_answered_call_inr)} loading={loading} />
      </div>

      {loading && !data ? (
        <LoadingGrid />
      ) : data ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Section title="Funnel" description="From ring to booking. * = only calls handled since telemetry shipped.">
            <Funnel steps={data.funnel} />
          </Section>
          <Section
            title="Spend"
            description={`${fmtMoney(data.kpis.cost_inr)} in this range`}
            action={
              <Link href="/admin/usage" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                Details <ArrowRight className="size-3.5" />
              </Link>
            }
          >
            <Histogram
              buckets={data.daily_cost.map((d, i) => ({ from: i, to: i + 1, count: Math.round(d.cost_inr) }))}
              label={(b) =>
                data.daily_cost.length <= 14 || b.from % Math.ceil(data.daily_cost.length / 10) === 0
                  ? data.daily_cost[b.from].date.slice(5)
                  : ""
              }
              colorVar="--accent-warm"
            />
          </Section>
          <Section
            title="Phase 1B decisions"
            description="What the shadow data says about each candidate audio fix"
            className="lg:col-span-2"
            action={
              <Link href="/admin/audio" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                Audio detail <ArrowRight className="size-3.5" />
              </Link>
            }
          >
            <DecisionList decisions={data.decisions} />
          </Section>
          <div className="lg:col-span-2">
            <CoverageNote>
              {fmtNum(data.coverage.calls_with_audio_telemetry)} of {fmtNum(data.coverage.calls)} calls in this range carry
              audio telemetry and {fmtNum(data.coverage.calls_with_call_metrics)} carry usage/performance telemetry. Older calls
              only contribute to the metrics that come from existing call, lead and escalation records.
            </CoverageNote>
          </div>
        </div>
      ) : null}
    </>
  );
}
