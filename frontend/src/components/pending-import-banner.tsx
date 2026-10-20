"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { useOnboarding } from "@/lib/onboarding-context";
import { toneCssVar } from "@/lib/tone";

const POLL_INTERVAL_MS = 5000;

/**
 * Shows the first-property Airbnb import started during onboarding while
 * it's still running. The import itself is finished by the server (backend
 * app/services/onboarding_service.py) -- this only watches GET /onboarding,
 * so a refresh, a closed tab or a sign-in on another device never loses the
 * property; the banner just picks the status back up. Mounted once in the
 * dashboard layout.
 */
export function PendingImportBanner() {
  const { state, refetch } = useOnboarding();
  const importStatus = state?.first_property?.status ?? null;
  // Only announce an outcome this page actually watched happen -- not on
  // every dashboard load after an import finished days ago.
  const sawImportingRef = useRef(false);

  useEffect(() => {
    if (importStatus === "importing") {
      sawImportingRef.current = true;
      const timer = window.setInterval(() => void refetch(), POLL_INTERVAL_MS);
      return () => window.clearInterval(timer);
    }
    if (!sawImportingRef.current) return;
    sawImportingRef.current = false;
    const record = state?.first_property;
    if (importStatus === "completed") {
      toast.success(`${record?.property_name ?? "Your property"} was imported successfully.`);
    } else if (importStatus === "failed") {
      toast.error(`Couldn't import your property: ${record?.error ?? "unknown error"}. Add it from Properties.`);
    }
  }, [importStatus, refetch, state?.first_property]);

  if (importStatus !== "importing") return null;

  return (
    <Card
      className="mb-4"
      style={{
        borderColor: `color-mix(in srgb, ${toneCssVar.progress} 30%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${toneCssVar.progress} 5%, transparent)`,
      }}
    >
      <CardContent className="flex items-center gap-3 py-3 text-sm">
        <span
          className="h-2 w-2 shrink-0 animate-pulse rounded-full"
          style={{ backgroundColor: toneCssVar.progress }}
        />
        Importing your property from Airbnb — this can take a minute. You can keep using the dashboard meanwhile.
      </CardContent>
    </Card>
  );
}
