"use client";

import { useContext } from "react";
import { DateRangeContext } from "@/components/date-range-context";

export function useDateRange() {
  const ctx = useContext(DateRangeContext);
  if (!ctx) throw new Error("useDateRange must be used within a DateRangeProvider");
  return ctx;
}
