"use client";

import Link from "next/link";
import { Info } from "lucide-react";
import { useCapability } from "@/lib/capabilities-context";
import { cn } from "@/lib/utils";

/**
 * Explains, in place, that a section belongs to a capability that's
 * currently off (or not available) -- while leaving the section itself
 * usable, since its data and settings are kept and apply again the moment
 * the capability is back on. Renders nothing while capability state is
 * loading or unknown, so a failed fetch never shows a false warning.
 */
export function CapabilityNotice({
  capabilityId,
  whenOff,
  whenUnavailable,
  className,
}: {
  capabilityId: string;
  whenOff?: string;
  whenUnavailable?: string;
  className?: string;
}) {
  const cap = useCapability(capabilityId);
  if (!cap) return null;
  const message = cap.state === "unavailable" ? whenUnavailable : !cap.enabled ? whenOff : null;
  if (!message) return null;

  return (
    <div
      role="note"
      className={cn("flex items-start gap-2 rounded-lg border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground", className)}
    >
      <Info className="mt-0.5 size-3.5 shrink-0" />
      <p>
        {message}{" "}
        {cap.selectable && cap.state !== "unavailable" && (
          <Link href="/dashboard/settings?tab=features" className="font-medium text-primary hover:underline">
            Turn it on in Features &amp; modules
          </Link>
        )}
      </p>
    </div>
  );
}
