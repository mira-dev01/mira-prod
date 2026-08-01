import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import type { CallSummary } from "@/lib/types";

// AI summary outcome vocabulary -- see backend/app/schemas/call_summary.py's
// SummaryOutcome.status for the full literal list this maps.
const outcomeTone: Record<string, StatusTone> = {
  "Booking Confirmed": "live",
  "Booking Likely": "live",
  "Awaiting Guest Details": "pending",
  "Awaiting Host Action": "pending",
  "Follow-up Required": "pending",
  "Guest Dropped": "destructive",
  "No Booking": "neutral",
  "Spam / Junk Call": "destructive",
};

function SummaryField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  );
}

/**
 * Shared "AI summary" card -- same rendering used by the call detail page
 * and the Booking Requests lead panel, so a call's summary looks identical
 * regardless of where the host opens it from.
 */
export function CallSummaryCard({ summary }: { summary: CallSummary | null }) {
  return (
    <Card className="surface-interactive border-primary/20 bg-primary/5">
      <CardHeader>
        <CardTitle>AI summary</CardTitle>
      </CardHeader>
      <CardContent>
        {!summary ? (
          <p className="text-base leading-relaxed text-muted-foreground">No summary available.</p>
        ) : (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={summary.outcome.status} tone={outcomeTone[summary.outcome.status] ?? "neutral"} />
              <p className="text-sm text-muted-foreground">{summary.outcome.reason}</p>
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold">Booking snapshot</h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                <SummaryField label="Guest" value={summary.booking_snapshot.guest_name} />
                <SummaryField label="Intent" value={summary.booking_snapshot.intent} />
                <SummaryField label="Check-in" value={summary.booking_snapshot.check_in} />
                <SummaryField label="Check-out" value={summary.booking_snapshot.check_out} />
                <SummaryField label="Nights" value={summary.booking_snapshot.nights} />
                <SummaryField label="Guests" value={summary.booking_snapshot.guests} />
                <SummaryField label="Budget" value={summary.booking_snapshot.budget} />
                <SummaryField label="Occasion" value={summary.booking_snapshot.occasion} />
                <SummaryField label="Language" value={summary.booking_snapshot.language} />
                <SummaryField
                  label="Property discussed"
                  value={
                    summary.booking_snapshot.property.length > 0
                      ? summary.booking_snapshot.property.join(", ")
                      : "Not discussed"
                  }
                />
              </div>
            </div>

            <div>
              <h3 className="mb-1 text-sm font-semibold">Conversation summary</h3>
              <p className="text-sm leading-relaxed">{summary.conversation_summary}</p>
            </div>

            {summary.host_action.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold">Host action required</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {summary.host_action.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {summary.key_details.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold">Key details mentioned</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {summary.key_details.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <h3 className="mb-1 text-sm font-semibold">Missing information</h3>
              {summary.missing_information.length > 0 ? (
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {summary.missing_information.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">None</p>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
