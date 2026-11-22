"use client";

import Link from "next/link";
import { ArrowRight, Phone, Users } from "lucide-react";
import { InfoCard } from "@/components/overview/info-card";
import { StatusChip } from "@/components/status-chip";
import { WhatsAppButton } from "@/components/whatsapp-button";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatStayDates, nightsBetween } from "@/lib/format";
import { formatLeadTimestamp, leadGuestLabel, useLeadLabels } from "@/lib/leads";
import { telLink } from "@/lib/utils";
import type { LeadOut, OverviewMetricInfo } from "@/lib/types";

function details(lead: LeadOut): string {
  const parts: string[] = [];
  if (lead.properties_discussed[0]) parts.push(lead.properties_discussed[0]);
  if (lead.check_in && lead.check_out) {
    parts.push(formatStayDates(lead.check_in, lead.check_out));
    const n = nightsBetween(lead.check_in, lead.check_out);
    if (n > 0) parts.push(`${n} night${n === 1 ? "" : "s"}`);
  }
  if (lead.num_guests) parts.push(`${lead.num_guests} guest${lead.num_guests === 1 ? "" : "s"}`);
  return parts.length ? parts.join(" · ") : "No details yet";
}

/** Open leads right now (open or contacted), newest first. A row opens the lead panel. */
export function LeadsCard({
  leads,
  info,
  loading,
  onOpenLead,
  limit = 5,
  className,
}: {
  leads: LeadOut[];
  info: OverviewMetricInfo | undefined;
  loading: boolean;
  onOpenLead: (lead: LeadOut) => void;
  limit?: number;
  className?: string;
}) {
  const { label, tone } = useLeadLabels();
  const open = leads
    .filter((l) => l.status === "open" || l.status === "contacted")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const rows = open.slice(0, limit);

  return (
    <InfoCard info={info} className={`flex flex-col gap-4 p-5 ${className ?? ""}`}>
      {(infoButton) => (
        <>
          <h2 className="flex items-center gap-2 font-heading text-[15px] leading-5 font-semibold text-heading">
            <Users className="size-4 text-primary" aria-hidden />
            <Link href="/dashboard/leads?tab=booking" className="hover:underline">
              Leads
            </Link>
            {infoButton}
          </h2>
          {loading ? (
            <Skeleton className="h-40 w-full" />
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">No open leads right now.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/60">
                    <TableHead>Guest</TableHead>
                    <TableHead>Date &amp; time</TableHead>
                    <TableHead>Details</TableHead>
                    <TableHead>Lead type</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((lead) => {
                    const tel = telLink(lead.phone);
                    return (
                      <TableRow key={lead.id} className="cursor-pointer" onClick={() => onOpenLead(lead)}>
                        <TableCell className="font-medium">{leadGuestLabel(lead)}</TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">{formatLeadTimestamp(lead.created_at)}</TableCell>
                        <TableCell className="text-muted-foreground">{details(lead)}</TableCell>
                        <TableCell>
                          <StatusChip status={label(lead.lead_temperature)} tone={tone(lead.lead_temperature)} className="normal-case" />
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center justify-end gap-1.5">
                            <WhatsAppButton phone={lead.phone} stopPropagation />
                            {tel && (
                              <Button
                                nativeButton={false}
                                variant="outline"
                                size="icon-sm"
                                aria-label="Call guest"
                                title="Call guest"
                                render={<a href={tel} onClick={(e) => e.stopPropagation()} />}
                              >
                                <Phone className="text-primary" />
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
          <Link href="/dashboard/leads?tab=booking" className="mt-auto flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            View all leads{open.length > rows.length ? ` (${open.length})` : ""} <ArrowRight className="size-3.5" />
          </Link>
        </>
      )}
    </InfoCard>
  );
}
