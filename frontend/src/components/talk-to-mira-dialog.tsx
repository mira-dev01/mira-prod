"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync } from "@/hooks/use-async";
import { api, API_BASE_URL, getToken } from "@/lib/api";

const PORTFOLIO_VALUE = "__portfolio__";

/**
 * Opens the same backend browser-test page (GET /api/v1/voice/test) that
 * Properties page's per-card "Test in browser" and "Test full portfolio in
 * browser" buttons use -- with or without property_id, respectively. This is
 * the one shared entry point for both, so the sidebar's global "Talk to
 * Mira" and the Properties page always drive the exact same call flow.
 */
function openVoiceTest(propertyId: string | null) {
  const token = getToken();
  if (!token) {
    toast.error("Not logged in");
    return;
  }
  const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
  const params = new URLSearchParams({ token });
  if (propertyId) params.set("property_id", propertyId);
  window.open(`${backendOrigin}/api/v1/voice/test?${params.toString()}`, "_blank");
}

export function TalkToMiraDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  // Only fetch once the dialog is actually opened -- this component is
  // mounted unconditionally (in the sidebar, on every dashboard page) so an
  // unguarded useAsync would fire api.properties.list() on every page load
  // whether or not the host ever opens the picker.
  const { data: properties, loading } = useAsync(() => (open ? api.properties.list() : Promise.resolve([])), [open]);
  const [selected, setSelected] = useState<string>(PORTFOLIO_VALUE);

  function handleStart() {
    openVoiceTest(selected === PORTFOLIO_VALUE ? null : selected);
    onOpenChange(false);
  }

  return (
    <RightPanel
      open={open}
      onOpenChange={onOpenChange}
      title="Talk to Mira"
      footer={<RightPanelFooterButton onClick={handleStart}>Start test call</RightPanelFooterButton>}
    >
      <p className="text-sm text-muted-foreground">
        Test the voice agent in your browser, as a guest calling in would hear it.
      </p>
      <div className="space-y-2">
        <Label>Property</Label>
        <Select value={selected} onValueChange={(v) => v && setSelected(v)}>
          <SelectTrigger disabled={loading} className="w-full">
            <SelectValue placeholder="Full portfolio (Lead Agent)">
              {(value: string) =>
                value === PORTFOLIO_VALUE
                  ? "Full portfolio (Lead Agent)"
                  : properties?.find((p) => p.id === value)?.name ?? "Full portfolio (Lead Agent)"
              }
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={PORTFOLIO_VALUE}>Full portfolio (Lead Agent)</SelectItem>
            {properties?.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </RightPanel>
  );
}
