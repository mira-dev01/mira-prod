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

function defaultRange(): { start: Date; end: Date } {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 29);
  return { start, end };
}

export function DateRangeProvider({ children }: { children: React.ReactNode }) {
  const initial = defaultRange();
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
