"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ActionableCard, type ActionableCardPriority } from "@/components/actionable-card";
import { API_BASE_URL, getToken } from "@/lib/api";
import type { NotificationOut } from "@/lib/types";

const priorityMap: Record<string, ActionableCardPriority> = {
  emergency: { label: "High", tone: "high" },
  high: { label: "High", tone: "high" },
  medium: { label: "Medium", tone: "medium" },
  low: { label: "Low", tone: "low" },
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

export function NotificationsFeed({ initial, activeCount }: { initial: NotificationOut[]; activeCount?: number }) {
  const [notifications, setNotifications] = useState<NotificationOut[]>(initial);
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

  const count = activeCount ?? notifications.filter((n) => n.status === "new").length;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Live requests</CardTitle>
        <Badge variant="outline">{count} active</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {notifications.length === 0 && (
          <p className="text-sm text-muted-foreground">No notifications yet — they will appear here in real time.</p>
        )}
        {notifications.map((n) => (
          <ActionableCard
            key={n.id}
            title={`${capitalize(n.channel)} — ${truncate(n.message, 40)}`}
            summary={n.message}
            metadata={`${n.channel} · ${new Date(n.created_at).toLocaleString()}`}
            priority={priorityMap[n.urgency] ?? { label: capitalize(n.urgency), tone: "low" }}
          />
        ))}
      </CardContent>
    </Card>
  );
}
