"use client";

import { useEffect } from "react";
import { API_BASE_URL, getToken } from "@/lib/api";
import type { NotificationOut, TicketOut } from "@/lib/types";

/**
 * Single shared subscription to GET /notifications/stream (the SSE-shaped
 * transport every "something changed, go refetch" consumer rides -- see
 * live-requests-card.tsx's own docstring on why this stream is a signal,
 * not a data source). Originally each consumer (LiveRequestsCard,
 * OpportunitiesCard, the Opportunities page) opened its OWN fetch()
 * connection and re-parsed the whole event stream independently -- two or
 * three simultaneous open connections per dashboard page load for zero
 * additional functionality. This module holds ONE real connection at a
 * time (module-level, not per-component) and fans parsed notifications out
 * to every subscribed callback; the connection opens on the first
 * subscriber and closes once the last one unmounts.
 */
type Listener = (notification: NotificationOut) => void;
// Every new ticket also arrives on this connection, as its own `event: ticket` message (a ticket is what the host has
// to do, whether or not an alert was raised with it), so each one reaches the dashboard on its own.
type TicketListener = (ticket: TicketOut) => void;

const listeners = new Set<Listener>();
const ticketListeners = new Set<TicketListener>();
const hasSubscribers = () => listeners.size + ticketListeners.size > 0;
let controller: AbortController | null = null;
// Dedup lives here now, once, for the single shared connection -- previously
// each of the 2-3 independent consumers kept its own seenNotificationIds
// set, which was really guarding against the same underlying connection
// redelivering an id, not against multiple consumers seeing the same event
// (that's expected and fine; every subscriber SHOULD see every notification).
let seenNotificationIds = new Set<string>();
let seenTicketIds = new Set<string>();

// Principal-review fix: connect() previously had no reconnect logic at all
// -- a dropped connection (backend restart, network blip, laptop sleep/
// wake) silently ended the stream and every subscribed panel (Live
// Requests, Opportunities, Recovery Analytics) went permanently stale with
// no visible indication, until a full page reload. Exponential backoff
// (2s -> 4s -> 8s -> ... capped at 30s) so a genuinely down backend isn't
// hammered with reconnect attempts, but a transient blip recovers quickly.
const _RECONNECT_BASE_DELAY_MS = 2000;
const _RECONNECT_MAX_DELAY_MS = 30000;
let reconnectAttempt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function parseSseEvent(eventText: string): { kind: "notification" | "ticket"; data: unknown } | null {
  const lines = eventText.split("\n");
  const line = lines.find((l) => l.startsWith("data: "));
  if (!line) return null;
  const kind = lines.some((l) => l.trim() === "event: ticket") ? "ticket" : "notification";
  try {
    return { kind, data: JSON.parse(line.slice("data: ".length)) };
  } catch {
    return null;
  }
}

async function connect() {
  const token = await getToken();
  controller = new AbortController();
  const { signal } = controller;
  seenNotificationIds = new Set<string>();
  seenTicketIds = new Set<string>();

  try {
    const res = await fetch(`${API_BASE_URL}/notifications/stream`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      signal,
    });
    if (!res.body) {
      scheduleReconnect();
      return;
    }

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
        const parsed = parseSseEvent(event);
        if (!parsed) continue;
        if (parsed.kind === "ticket") {
          const ticket = parsed.data as TicketOut;
          if (seenTicketIds.has(ticket.id)) continue;
          seenTicketIds.add(ticket.id);
          reconnectAttempt = 0;
          ticketListeners.forEach((listener) => listener(ticket));
          continue;
        }
        const notification = parsed.data as NotificationOut;
        if (seenNotificationIds.has(notification.id)) continue;
        seenNotificationIds.add(notification.id);
        // A real message arrived -- the connection is healthy, reset backoff.
        reconnectAttempt = 0;
        listeners.forEach((listener) => listener(notification));
      }
    }
    // The stream ended (`done`) without the caller aborting it -- the
    // backend closed/restarted. Reconnect rather than going silent.
    scheduleReconnect();
  } catch {
    // AbortError (last subscriber unmounted -- don't reconnect) is
    // indistinguishable here from a real network failure without checking
    // the signal explicitly, so check it: intentional teardown must not
    // schedule a reconnect no one asked for.
    if (!signal.aborted) scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!hasSubscribers() || reconnectTimer !== null) return;
  const delay = Math.min(_RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempt, _RECONNECT_MAX_DELAY_MS);
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (hasSubscribers()) connect();
  }, delay);
}

function subscribe<L>(set: Set<L>, listener: L): () => void {
  set.add(listener);
  if (listeners.size + ticketListeners.size === 1) {
    reconnectAttempt = 0;
    connect();
  }

  return () => {
    set.delete(listener);
    if (!hasSubscribers()) {
      controller?.abort();
      controller = null;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    }
  };
}

/**
 * Subscribes `onNotification` to the shared stream for the lifetime of the
 * calling component. Every notification is delivered to every subscriber --
 * a component that only cares about one channel (e.g. "busy_recovery")
 * filters inside its own callback, same as before, just without owning a
 * second physical connection to do it.
 */
export function useNotificationStream(onNotification: Listener) {
  useEffect(() => {
    return subscribe(listeners, onNotification);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onNotification is expected to be a stable callback (useCallback/inline-but-referentially-cheap), re-subscribing on every render would defeat the shared-connection purpose
  }, []);
}

/** Subscribes `onTicket` to every new ticket the host's dashboard is told about, on the same shared connection. */
export function useTicketStream(onTicket: TicketListener) {
  useEffect(() => {
    return subscribe(ticketListeners, onTicket);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- same reason as useNotificationStream: one stable subscription
  }, []);
}
