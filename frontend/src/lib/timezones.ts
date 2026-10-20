// A short, curated IANA timezone list -- an India-first host fleet plus the
// common Airbnb host regions, not the full IANA database. The backend
// (app/schemas/user.py's host_call_hours_timezone validator, and
// app/schemas/property.py) is authoritative regardless of what's offered
// here. Previously inlined in components/settings/call-ownership-card.tsx
// and components/property-form-fields.tsx -- extracted so the Settings
// host-call-hours editor uses the same list.
export const TIMEZONES = [
  { value: "Asia/Kolkata", label: "India Standard Time (Asia/Kolkata)" },
  { value: "Asia/Dubai", label: "Gulf Standard Time (Asia/Dubai)" },
  { value: "Asia/Kathmandu", label: "Nepal Time (Asia/Kathmandu)" },
  { value: "Asia/Colombo", label: "Sri Lanka Time (Asia/Colombo)" },
  { value: "Europe/London", label: "UK Time (Europe/London)" },
  { value: "America/New_York", label: "US Eastern Time (America/New_York)" },
  { value: "America/Los_Angeles", label: "US Pacific Time (America/Los_Angeles)" },
  { value: "Asia/Singapore", label: "Singapore Time (Asia/Singapore)" },
  { value: "Australia/Sydney", label: "Australia Eastern Time (Australia/Sydney)" },
  { value: "UTC", label: "UTC" },
] as const;

export function timezoneLabel(tz: string): string {
  return TIMEZONES.find((t) => t.value === tz)?.label ?? tz;
}

/** "22:30" -> "10:30 PM"; "" / null -> "--". */
export function formatHourMinute(value: string | null | undefined): string {
  if (!value) return "--";
  const [hourStr, minuteStr] = value.split(":");
  const hour = Number(hourStr);
  if (Number.isNaN(hour)) return value;
  const suffix = hour >= 12 ? "PM" : "AM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${hour12}:${minuteStr} ${suffix}`;
}
