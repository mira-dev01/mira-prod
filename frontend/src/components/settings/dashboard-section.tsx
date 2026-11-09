"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Cell, CellLead, CellTitle, Hint, Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { ApiError, api } from "@/lib/api";
import { useNavigation } from "@/lib/navigation-context";

/**
 * Settings > Dashboard: one place to find both layout editors and their
 * resets. Layout only -- turning features on/off stays in Features &
 * modules (no second enable/disable implementation here).
 */
export function DashboardSection() {
  return (
    <Sheet>
      <Cell className="gap-3">
        <CellTitle>Hidden or turned off?</CellTitle>
        <div className="grid gap-4 text-sm leading-5 md:grid-cols-2 md:gap-8">
          <p>
            <span className="font-bold">Hidden</span> just tidies your dashboard: the page or widget is out of
            sight, but the feature behind it keeps working for your guests.
          </p>
          <p className="text-muted-foreground">
            <span className="font-bold text-foreground">Turned off</span> changes what Mira does, and is managed in{" "}
            <Link href="/dashboard/settings?tab=features" className="font-semibold text-foreground underline underline-offset-2 hover:no-underline">
              Features &amp; modules
            </Link>
            . Pages and widgets that need a feature you&apos;ve turned off can&apos;t be added until it&apos;s back on.
          </p>
        </div>
      </Cell>
      <NavigationCard />
      <OverviewCard />
    </Sheet>
  );
}

function NavigationCard() {
  const { prefs, loading, error, reset, setEditing, refetch } = useNavigation();
  const [resetting, setResetting] = useState(false);

  async function handleReset() {
    setResetting(true);
    try {
      await reset();
      toast.success("Navigation reset to default");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset your navigation");
    } finally {
      setResetting(false);
    }
  }

  const hidden = prefs?.items.filter((i) => i.available && i.hidden) ?? [];
  const unavailable = prefs?.items.filter((i) => !i.available) ?? [];

  return (
    <Cell className="md:col-span-6">
      <CellTitle>Navigation</CellTitle>
      {loading && !prefs ? (
        <Skeleton variant="text" />
      ) : error && !prefs ? (
        <div className="space-y-2">
          <Hint>Couldn&apos;t load your navigation settings.</Hint>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Try again
          </Button>
        </div>
      ) : prefs ? (
        <>
          <div className="flex flex-col gap-1">
            <CellLead>Reorder the sidebar and hide pages you don&apos;t use.</CellLead>
            <Hint>
              Overview and Settings always stay.
              {prefs.is_default ? " You're using the default layout." : ""}
            </Hint>
          </div>
          {hidden.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[13px] font-semibold text-muted-foreground">Hidden from the sidebar</p>
              <ul className="space-y-1 text-sm">
                {hidden.map((item) => (
                  <li key={item.id} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <EyeOff className="size-3.5 text-muted-foreground" />
                      {item.label}
                    </span>
                    <Link href={item.href} className="text-[13px] font-semibold text-foreground underline underline-offset-2 hover:no-underline">
                      Open
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {unavailable.length > 0 && (
            <ul className="space-y-1 text-[13px] text-muted-foreground">
              {unavailable.map((item) => (
                <li key={item.id}>
                  <span className="font-semibold text-foreground">{item.label}</span> — {item.unavailable_reason}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-auto flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setEditing(true)}>
              Customize navigation
            </Button>
            <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting || prefs.is_default}>
              Reset to default
            </Button>
          </div>
        </>
      ) : null}
    </Cell>
  );
}

function OverviewCard() {
  const { data: layout, loading, error, refetch, setData } = useAsync(() => api.preferences.overviewWidgets(), []);
  const [resetting, setResetting] = useState(false);

  async function handleReset() {
    setResetting(true);
    try {
      setData(await api.preferences.resetOverviewWidgets());
      toast.success("Overview reset to the recommended layout");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset your Overview layout");
    } finally {
      setResetting(false);
    }
  }

  const shown = layout?.widgets.filter((w) => w.available && !w.hidden) ?? [];
  const unavailable = layout?.widgets.filter((w) => !w.available) ?? [];

  return (
    <Cell className="md:col-span-6">
      <CellTitle>Overview widgets</CellTitle>
      {loading && !layout ? (
        <Skeleton variant="text" />
      ) : error && !layout ? (
        <div className="space-y-2">
          <Hint>Couldn&apos;t load your Overview layout.</Hint>
          <Button variant="outline" size="sm" onClick={refetch}>
            Try again
          </Button>
        </div>
      ) : layout ? (
        <>
          <div className="flex flex-col gap-1">
            <CellLead>
              {shown.length} widget{shown.length === 1 ? "" : "s"} on your Overview
              {layout.is_default ? " (recommended layout)" : ""}.
            </CellLead>
            <Hint>Add, hide, reorder and resize them from the Overview page.</Hint>
          </div>
          {unavailable.length > 0 && (
            <ul className="space-y-1 text-[13px] text-muted-foreground">
              {unavailable.map((w) => (
                <li key={w.id}>
                  <span className="font-semibold text-foreground">{w.name}</span> — {w.unavailable_reason}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-auto flex flex-wrap gap-2">
            <Button size="sm" nativeButton={false} render={<Link href="/dashboard?customize=1" />}>
              Customize Overview
              <ArrowRight className="size-3.5" />
            </Button>
            <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting || layout.is_default}>
              Reset to recommended
            </Button>
          </div>
        </>
      ) : null}
    </Cell>
  );
}
