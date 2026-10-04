"use client";

import { createContext, useMemo, useState } from "react";

export type DateRangeContextValue = {
  startDate: Date;
  endDate: Date;
  startDateISO: string;
  endDateISO: string;
  setRange: (start: Date, end: Date) => void;
};

export const DateRangeContext = createContext<DateRangeContextValue | null>(null);

function toISODate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function defaultRange(days: number): { start: Date; end: Date } {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - (days - 1));
  return { start, end };
}

export function DateRangeProvider({
  children,
  defaultDays = 30,
}: {
  children: React.ReactNode;
  /** Initial window length in days, ending today. The host dashboard keeps
   * the 30-day default; the admin panel opens on 7 (the shadow week). */
  defaultDays?: number;
}) {
  const initial = defaultRange(defaultDays);
  const [startDate, setStartDate] = useState(initial.start);
  const [endDate, setEndDate] = useState(initial.end);

  const value = useMemo<DateRangeContextValue>(
    () => ({
      startDate,
      endDate,
      startDateISO: toISODate(startDate),
      endDateISO: toISODate(endDate),
      setRange: (start: Date, end: Date) => {
        setStartDate(start);
        setEndDate(end);
      },
    }),
    [startDate, endDate]
  );

  return <DateRangeContext.Provider value={value}>{children}</DateRangeContext.Provider>;
}
