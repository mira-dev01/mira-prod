/**
 * Number/date formatting shared by the Analytics page and the booking
 * reconciliation panel. Indian grouping (en-IN) throughout -- every price in
 * this product is INR.
 */

export function formatINR(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

/** Compact lakh/crore form for headline numbers (₹2.4L, ₹1.1Cr). */
export function formatINRCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e7) return `₹${trim(value / 1e7)}Cr`;
  if (abs >= 1e5) return `₹${trim(value / 1e5)}L`;
  return formatINR(value);
}

function trim(value: number): string {
  return value.toFixed(value >= 100 ? 0 : 1).replace(/\.0$/, "");
}

export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** "18 Oct – 20 Oct" from two YYYY-MM-DD strings, parsed as plain dates (no timezone shift). */
export function formatStayDates(checkIn: string, checkOut: string): string {
  const fmt = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  };
  return `${fmt(checkIn)} – ${fmt(checkOut)}`;
}

export function nightsBetween(checkIn: string, checkOut: string): number {
  const toUtc = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((toUtc(checkOut) - toUtc(checkIn)) / 86_400_000);
}
