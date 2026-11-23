"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useNotificationStream, useTicketStream } from "@/hooks/use-notification-stream";
import type { ActiveBooking, NotificationOut, TicketOut, TicketSummary } from "@/lib/types";

/**
 * What needs the host's attention on the Leads page, made visible everywhere in the dashboard: the red numbers on the
 * Checked-in and Tickets tabs and on the sidebar's Leads link, and, when something new arrives while the dashboard is
 * open, a banner that fades by itself plus a short ping (the ping follows the ringer, the bell on those tabs).
 *
 * Two things arrive on the notification stream (hooks/use-notification-stream.ts):
 *  - a TICKET, for every one that is opened (what the host has to do after a call): one banner each;
 *  - a REQUEST from a guest with an active booking that is not a ticket (a notification the server tagged with
 *    `booking_lead_id`): one banner each.
 * A request that has a ticket is announced once, as the ticket (`alerted` keeps the two from both ringing).
 *
 * The numbers come from the server and are re-read after every event and once a minute, so they are right after a
 * reload and after the host resolves something:
 *   activityCount  unread requests from booked guests that are not tickets
 *   tickets.open   every open ticket;  tickets.booked_open  those that belong to a booked guest
 * and from them: checkedInCount (the Checked-in tab), ticketCount (the Tickets tab), navCount (the sidebar).
 * `version` goes up whenever any of it changes, so a list that is open can read itself again.
 */

const SOUND_KEY = "mira.bookingActivitySound";
const POLL_MS = 60_000;

type BookingActivityValue = {
  activityCount: number;
  tickets: TicketSummary;
  checkedInCount: number;
  ticketCount: number;
  navCount: number;
  version: number;
  refresh: () => Promise<void>;
  soundOn: boolean;
  setSoundOn: (on: boolean) => void;
};

const NO_TICKETS: TicketSummary = { open: 0, booked_open: 0 };

const BookingActivityContext = createContext<BookingActivityValue>({
  activityCount: 0,
  tickets: NO_TICKETS,
  checkedInCount: 0,
  ticketCount: 0,
  navCount: 0,
  version: 0,
  refresh: async () => {},
  soundOn: true,
  setSoundOn: () => {},
});

export function useBookingActivity(): BookingActivityValue {
  return useContext(BookingActivityContext);
}

// One audio context for the page; browsers only let it start after the host has clicked something once, so it is
// created lazily and resumed on every ping. A blocked or failed ping is silent: the banner and the number still show.
let audio: AudioContext | null = null;

function playPing() {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    audio = audio ?? new Ctx();
    void audio.resume();
    const now = audio.currentTime;
    [880, 1318.5].forEach((frequency, i) => {
      const oscillator = audio!.createOscillator();
      const gain = audio!.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      const start = now + i * 0.14;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.28);
      oscillator.connect(gain).connect(audio!.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.3);
    });
  } catch {
    /* no sound is not an error */
  }
}

function readSoundPreference(): boolean {
  try {
    return window.localStorage.getItem(SOUND_KEY) !== "off";
  } catch {
    return true;
  }
}

export function BookingActivityProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [activityCount, setActivityCount] = useState(0);
  const [tickets, setTickets] = useState<TicketSummary>(NO_TICKETS);
  const [version, setVersion] = useState(0);
  const [soundOn, setSoundOnState] = useState(true);
  const last = useRef({ activity: 0, open: 0, booked: 0 });
  // Notifications already announced (through their ticket or on their own), and the ones waiting a moment to see if
  // a ticket follows.
  const alerted = useRef(new Set<string>());
  const waiting = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const soundRef = useRef(true);

  useEffect(() => {
    const stored = readSoundPreference();
    soundRef.current = stored;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the saved choice lives in the browser, so it can only be read after mount
    setSoundOnState(stored);
  }, []);

  const setSoundOn = useCallback((on: boolean) => {
    soundRef.current = on;
    setSoundOnState(on);
    try {
      window.localStorage.setItem(SOUND_KEY, on ? "on" : "off");
    } catch {
      /* the choice then lasts until the page is closed */
    }
    if (on) playPing(); // this click also lets the browser start audio
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [{ count: activity }, summary] = await Promise.all([api.activeBookings.activityCount(), api.tickets.summary()]);
      setActivityCount(activity);
      setTickets(summary);
      const before = last.current;
      last.current = { activity, open: summary.open, booked: summary.booked_open };
      if (before.activity !== activity || before.open !== summary.open || before.booked !== summary.booked_open) {
        setVersion((v) => v + 1);
      }
    } catch {
      /* keep the numbers we have; the next event or minute tick tries again */
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading the server's count after mount is the point of this effect
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && void refresh();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  const ping = useCallback(() => {
    if (soundRef.current) playPing();
  }, []);

  // A request from a booked guest that is not a ticket (or whose ticket has not shown up yet).
  const announceRequest = useCallback(
    async (notification: NotificationOut) => {
      let who: ActiveBooking | undefined;
      try {
        const list = await api.activeBookings.list();
        who = [...list.in_stay, ...list.upcoming].find((b) => b.lead_id === notification.booking_lead_id);
      } catch {
        /* the banner then just does not name the guest */
      }
      const name = who?.guest_name ?? "A guest with a booking";
      toast(`${name}${who?.phase === "in_stay" ? " (checked in)" : ""} has a request`, {
        description: notification.message,
        duration: 8000,
        action: { label: "View", onClick: () => router.push("/dashboard/leads?tab=checked-in") },
      });
      ping();
    },
    [router, ping]
  );

  const onNotification = useCallback(
    (notification: NotificationOut) => {
      if (!notification.booking_lead_id) return;
      void refresh();
      setVersion((v) => v + 1);
      // A ticket for this very request usually follows within the same breath: give it a moment to announce it,
      // so the host is told once.
      const id = notification.id;
      waiting.current.set(
        id,
        setTimeout(() => {
          waiting.current.delete(id);
          if (alerted.current.has(id)) return;
          alerted.current.add(id);
          void announceRequest(notification);
        }, 800)
      );
    },
    [refresh, announceRequest]
  );

  const onTicket = useCallback(
    (ticket: TicketOut) => {
      void refresh();
      setVersion((v) => v + 1);
      if (ticket.notification_id) {
        const pending = waiting.current.get(ticket.notification_id);
        if (pending) {
          clearTimeout(pending);
          waiting.current.delete(ticket.notification_id);
        }
        if (alerted.current.has(ticket.notification_id)) return; // already announced as the request
        alerted.current.add(ticket.notification_id);
      }
      const parts = [ticket.property_name, [ticket.guest_name, ticket.booking_phase === "in_stay" ? "checked in" : null].filter(Boolean).join(", ")].filter(Boolean);
      const onGuestRow = ticket.booking_phase === "in_stay" || ticket.booking_phase === "checked_out";
      toast(`New ticket: ${ticket.summary}`, {
        description: parts.join(" · ") || undefined,
        duration: ticket.urgency === "emergency" || ticket.urgency === "high" ? 12000 : 8000,
        action: { label: "View", onClick: () => router.push(onGuestRow ? "/dashboard/leads?tab=checked-in" : "/dashboard/leads?tab=tickets") },
      });
      ping();
    },
    [refresh, router, ping]
  );

  useNotificationStream(onNotification);
  useTicketStream(onTicket);

  const value = useMemo(
    () => ({
      activityCount,
      tickets,
      checkedInCount: activityCount + tickets.booked_open,
      ticketCount: tickets.open,
      navCount: activityCount + tickets.open,
      version,
      refresh,
      soundOn,
      setSoundOn,
    }),
    [activityCount, tickets, version, refresh, soundOn, setSoundOn]
  );
  return <BookingActivityContext.Provider value={value}>{children}</BookingActivityContext.Provider>;
}
