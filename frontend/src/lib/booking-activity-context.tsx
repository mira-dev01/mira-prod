"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import type { ActiveBooking, NotificationOut } from "@/lib/types";

/**
 * Requests from guests who have an ACTIVE BOOKING (Leads -> Active bookings), made visible everywhere in the
 * dashboard: the number on the Active bookings tab and on the sidebar's Leads link (`count`), and, when one
 * arrives while the host has the dashboard open, a banner that fades by itself plus a short ping.
 *
 * The server tags such a notification with `booking_lead_id` (backend active_booking_service.tag_activity); this
 * provider listens to the same notification stream the rest of the dashboard uses, so a request shows up within
 * a couple of seconds. `count` is the server's number of unread tagged requests, re-read after every event and
 * once a minute, so it is right after a reload and after the host clears a guest.
 */

const SOUND_KEY = "mira.bookingActivitySound";
const POLL_MS = 60_000;

type BookingActivityValue = {
  count: number;
  refresh: () => Promise<void>;
  soundOn: boolean;
  setSoundOn: (on: boolean) => void;
};

const BookingActivityContext = createContext<BookingActivityValue>({
  count: 0,
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
  const [count, setCount] = useState(0);
  const [soundOn, setSoundOnState] = useState(true);
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
      const { count: next } = await api.activeBookings.activityCount();
      setCount(next);
    } catch {
      /* keep the number we have; the next event or minute tick tries again */
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

  const onNotification = useCallback(
    async (notification: NotificationOut) => {
      if (!notification.booking_lead_id) return;
      void refresh();
      let who: ActiveBooking | undefined;
      try {
        const list = await api.activeBookings.list();
        who = [...list.in_stay, ...list.upcoming].find((b) => b.lead_id === notification.booking_lead_id);
      } catch {
        /* the banner then just does not name the guest */
      }
      const name = who?.guest_name ?? "A guest with a booking";
      toast(`${name}${who?.phase === "in_stay" ? " (in stay)" : ""} has a request`, {
        description: notification.message,
        duration: 8000,
        action: { label: "View", onClick: () => router.push("/dashboard/leads?tab=active") },
      });
      if (soundRef.current) playPing();
    },
    [refresh, router]
  );

  useNotificationStream(onNotification);

  const value = useMemo(() => ({ count, refresh, soundOn, setSoundOn }), [count, refresh, soundOn, setSoundOn]);
  return <BookingActivityContext.Provider value={value}>{children}</BookingActivityContext.Provider>;
}
