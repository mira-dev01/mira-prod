"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight, EyeOff, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    <div className="space-y-6">
      <Card className="max-w-3xl">
        <CardContent className="flex gap-3 py-4 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="space-y-1">
            <p>
              <span className="font-medium">Hidden</span> just tidies your dashboard: the page or widget is out of
              sight, but the feature behind it keeps working for your guests.
            </p>
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">Turned off</span> changes what Mira does, and is managed in{" "}
              <Link href="/dashboard/settings?tab=features" className="text-primary hover:underline">
                Features &amp; modules
              </Link>
              . Pages and widgets that need a feature you&apos;ve turned off can&apos;t be added until it&apos;s back on.
            </p>
          </div>
        </CardContent>
      </Card>
      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <NavigationCard />
        <OverviewCard />
      </div>
    </div>
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
    <Card>
      <CardHeader>
        <CardTitle>Navigation</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {loading && !prefs ? (
          <Skeleton variant="text" />
        ) : error && !prefs ? (
          <div className="space-y-2">
            <p className="text-muted-foreground">Couldn&apos;t load your navigation settings.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Try again
            </Button>
          </div>
        ) : prefs ? (
          <>
            <p className="text-muted-foreground">
              Reorder the sidebar and hide pages you don&apos;t use. Overview and Settings always stay.
              {prefs.is_default ? " You're using the default layout." : ""}
            </p>
            {hidden.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Hidden from the sidebar</p>
                <ul className="space-y-1">
                  {hidden.map((item) => (
                    <li key={item.id} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <EyeOff className="size-3.5 text-muted-foreground" />
                        {item.label}
                      </span>
                      <Link href={item.href} className="text-xs text-primary hover:underline">
                        Open
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {unavailable.length > 0 && (
              <ul className="space-y-1 text-xs text-muted-foreground">
                {unavailable.map((item) => (
                  <li key={item.id}>
                    <span className="font-medium text-foreground">{item.label}</span> — {item.unavailable_reason}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setEditing(true)}>
                Customize navigation
              </Button>
              <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting || prefs.is_default}>
                Reset to default
              </Button>
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
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
    <Card>
      <CardHeader>
        <CardTitle>Overview widgets</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {loading && !layout ? (
          <Skeleton variant="text" />
        ) : error && !layout ? (
          <div className="space-y-2">
            <p className="text-muted-foreground">Couldn&apos;t load your Overview layout.</p>
            <Button variant="outline" size="sm" onClick={refetch}>
              Try again
            </Button>
          </div>
        ) : layout ? (
          <>
            <p className="text-muted-foreground">
              {shown.length} widget{shown.length === 1 ? "" : "s"} on your Overview
              {layout.is_default ? " (recommended layout)" : ""}. Add, hide, reorder and resize them from the Overview
              page.
            </p>
            {unavailable.length > 0 && (
              <ul className="space-y-1 text-xs text-muted-foreground">
                {unavailable.map((w) => (
                  <li key={w.id}>
                    <span className="font-medium text-foreground">{w.name}</span> — {w.unavailable_reason}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2">
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
      </CardContent>
    </Card>
  );
}
