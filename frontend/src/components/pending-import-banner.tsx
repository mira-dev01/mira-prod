"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { PENDING_IMPORT_KEY } from "@/lib/auth-context";

type PendingImport = { snapshotId: string; icalUrl: string | null } | { error: string };

/**
 * Resumes the Airbnb scrape kicked off during host registration
 * (POST /auth/register-host never blocks on it -- see app/api/v1/auth.py).
 * Mounted once in the dashboard layout so it picks up the pending import
 * regardless of which page the host lands on after signup.
 */
// Read once, synchronously, before the first render -- so the component
// never needs to setState("polling") from inside an effect (React flags
// that as a cascading-render smell). Also clears the key immediately so a
// refresh mid-poll doesn't restart the whole flow.
function takePendingImport(): PendingImport | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(PENDING_IMPORT_KEY);
  if (!raw) return null;
  window.sessionStorage.removeItem(PENDING_IMPORT_KEY);
  try {
    return JSON.parse(raw) as PendingImport;
  } catch {
    return null;
  }
}

export function PendingImportBanner() {
  const [pending] = useState<PendingImport | null>(() => takePendingImport());
  const [state, setState] = useState<"idle" | "polling" | "done">(
    pending && !("error" in pending) ? "polling" : "idle"
  );

  useEffect(() => {
    if (!pending) return;

    if ("error" in pending) {
      toast.error(`Couldn't start importing your property: ${pending.error}. Add it manually from Properties.`);
      return;
    }

    let cancelled = false;

    (async () => {
      // Same 4s / ~5min-cap polling loop as the Properties page's "Import
      // from Airbnb" dialog (app/dashboard/properties/page.tsx) -- kept in
      // sync intentionally, not extracted into a shared hook for one caller.
      const maxAttempts = 75;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
        if (cancelled) return;

        try {
          const result = await api.properties.importAirbnbUrlsStatus(pending.snapshotId);
          if (result.status === "running") continue;

          if (result.status !== "ready") {
            toast.error("Importing your property failed — add it manually from the Properties page.");
            setState("done");
            return;
          }

          const created = result.results[0];
          if (created?.property && pending.icalUrl) {
            try {
              await api.properties.update(created.property.id, { ical_url: pending.icalUrl });
            } catch {
              toast.error("Property imported, but the iCal link couldn't be saved — add it from Properties.");
            }
          }

          if (created?.status === "error") {
            toast.error(`Couldn't import your property: ${created.error ?? "unknown error"}. Add it manually.`);
          } else {
            toast.success(`${created?.property?.name ?? "Your property"} was imported successfully.`);
          }
          setState("done");
          return;
        } catch (err) {
          toast.error(err instanceof ApiError ? err.message : "Import status check failed");
          setState("done");
          return;
        }
      }

      toast.error("Importing your property is taking longer than expected — check the Properties page shortly.");
      setState("done");
    })();

    return () => {
      cancelled = true;
    };
  }, [pending]);

  if (state !== "polling") return null;

  return (
    <Card className="mb-4 border-primary/30 bg-primary/5">
      <CardContent className="flex items-center gap-3 py-3 text-sm">
        <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
        Importing your property from Airbnb — this can take a minute. You can keep using the dashboard meanwhile.
      </CardContent>
    </Card>
  );
}
