"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_BASE_URL, getToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { NotificationOut } from "@/lib/types";

const urgencyVariant: Record<string, "destructive" | "outline"> = {
  emergency: "destructive",
  high: "destructive",
  medium: "outline",
  low: "outline",
};

const urgencyClassName: Record<string, string> = {
  medium: "badge-status-pending",
};

export function NotificationsFeed({ initial }: { initial: NotificationOut[] }) {
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

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live requests</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {notifications.length === 0 && (
          <p className="text-sm text-muted-foreground">No notifications yet — they will appear here in real time.</p>
        )}
        {notifications.map((n) => (
          <div key={n.id} className="flex items-start justify-between gap-3 border-b pb-3 last:border-0 last:pb-0">
            <div>
              <p className="text-sm">{n.message}</p>
              <p className="text-xs text-muted-foreground">
                {n.channel} · {new Date(n.created_at).toLocaleString()}
              </p>
            </div>
            <Badge variant={urgencyVariant[n.urgency] ?? "outline"} className={cn(urgencyClassName[n.urgency])}>
              {n.urgency}
            </Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
