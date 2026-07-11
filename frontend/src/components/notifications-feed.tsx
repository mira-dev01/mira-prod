"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { API_BASE_URL, api, ApiError, getToken } from "@/lib/api";
import type { NotificationOut } from "@/lib/types";

// "How urgent is this" -- not to be confused with lead temperature's
// hot/warm/cold (that's a sales signal, this is "does the host need to act
// right now"), so it gets its own tone mapping even though both reuse the
// same underlying StatusChip palette.
const urgencyTone: Record<string, StatusTone> = {
  emergency: "destructive",
  high: "destructive",
  medium: "pending",
  low: "neutral",
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function NotificationsFeed({ initial, activeCount }: { initial: NotificationOut[]; activeCount?: number }) {
  const [notifications, setNotifications] = useState<NotificationOut[]>(initial);
  const [resolving, setResolving] = useState<string | null>(null);
  const seenIds = useRef(new Set(initial.map((n) => n.id)));

  useEffect(() => {
    const controller = new AbortController();
    const token = getToken();

    async function streamNotifications() {
      try {
        const res = await fetch(`${API_BASE_URL}/notifications/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          signal: controller.signal,
        });
        if (!res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const events = buffer.split("\n\n");
          buffer = events.pop() ?? "";

          for (const event of events) {
            const line = event.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            const notification = JSON.parse(line.slice("data: ".length)) as NotificationOut;
            if (seenIds.current.has(notification.id)) continue;
            seenIds.current.add(notification.id);
            setNotifications((prev) => [notification, ...prev].slice(0, 50));
            toast.message(notification.message, { description: notification.channel });
          }
        }
      } catch {
        // connection closed (navigation, unmount, or backend restart) — nothing to do
      }
    }

    streamNotifications();
    return () => controller.abort();
  }, []);

  // Only what's still pending -- a host that already handled something
  // shouldn't have to keep scanning past it every time this panel updates.
  const activeNotifications = notifications.filter((n) => n.status === "new");
  const count = activeCount ?? activeNotifications.length;

  async function handleResolve(id: string) {
    setResolving(id);
    try {
      await api.notifications.markRead(id);
      setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, status: "read" } : n)));
      toast.success("Marked as handled");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update");
    } finally {
      setResolving(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Live requests</CardTitle>
        <Badge variant="outline">{count} active</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {activeNotifications.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {notifications.length === 0
              ? "No notifications yet — they will appear here in real time."
              : "Nothing pending — you're all caught up."}
          </p>
        )}
        {activeNotifications.map((n) => (
          <div key={n.id} className="space-y-2.5 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={n.urgency} tone={urgencyTone[n.urgency] ?? "neutral"} />
                <span className="text-sm font-medium">{n.property_name ?? capitalize(n.channel)}</span>
              </div>
              <span className="whitespace-nowrap text-xs text-muted-foreground">{formatTime(n.created_at)}</span>
            </div>
            {/* Full message, always -- never truncated. This is exactly the
                text the host needs to act on, and there's no dashboard-wide
                pattern here for "click to reveal the rest" that a
                non-technical host would reliably discover. */}
            <p className="text-sm leading-relaxed">{n.message}</p>
            <div className="flex items-center gap-2 pt-1">
              {n.call_session_id && (
                <Link href={`/dashboard/calls/${n.call_session_id}`}>
                  <Button variant="outline" size="sm">
                    View call
                  </Button>
                </Link>
              )}
              <Button size="sm" disabled={resolving === n.id} onClick={() => handleResolve(n.id)}>
                Mark as handled
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
