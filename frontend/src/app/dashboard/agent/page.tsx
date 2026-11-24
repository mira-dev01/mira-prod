"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Cell, Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BusyCallsStep } from "@/components/agent/busy-calls-step";
import { CollectStep } from "@/components/agent/collect-step";
import { HotLeadStep } from "@/components/agent/hot-lead-step";
import { NegotiationStep } from "@/components/agent/negotiation-step";
import { PaymentsStep } from "@/components/agent/payments-step";
import { VoiceStep } from "@/components/agent/voice-step";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AgentConfig } from "@/lib/types";

// One tab per question a host answers about their agent. Order follows a
// call: who counts as a lead worth chasing and what Mira asks them, what she
// can offer, how she sounds, how a booking gets confirmed and paid for.
const STEPS = [
  { id: "lead", label: "Lead qualification" },
  { id: "negotiation", label: "Negotiation policy" },
  { id: "voice", label: "Voice" },
  { id: "payments", label: "Payments" },
] as const;
type StepId = (typeof STEPS)[number]["id"];

function isStep(value: string | null): value is StepId {
  return STEPS.some((step) => step.id === value);
}

function AgentPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading: authLoading } = useAuth();
  const param = searchParams.get("step");
  const [step, setStep] = useState<StepId>(isStep(param) ? param : "lead");

  const { data, loading: configLoading, error } = useAsync(() => api.agentConfig.get(), []);
  // Saved values come back from the API; keep them here so each step starts
  // from what was just saved, not from what the page first loaded.
  const [saved, setSaved] = useState<AgentConfig | null>(null);
  const config = saved ?? data?.config ?? null;
  const [savedOverrides, setSavedOverrides] = useState<Record<string, Partial<AgentConfig["payment"]>> | null>(null);
  const overrides = savedOverrides ?? data?.payment_overrides ?? {};

  function handleStepChange(value: unknown) {
    if (typeof value !== "string" || !isStep(value)) return;
    setStep(value);
    router.replace(`/dashboard/agent?step=${value}`, { scroll: false });
  }

  const needsConfig = step === "lead" || step === "payments";
  const waiting = authLoading || (needsConfig && configLoading);

  return (
    <div className="space-y-6">
      <h1 className="page-title">Configure</h1>

      <Tabs value={step} onValueChange={handleStepChange}>
        <TabsList>
          {STEPS.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {waiting ? (
          <Sheet className="mt-4">
            <Cell>
              <Skeleton variant="text" className="w-1/3" />
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} variant="text" />
              ))}
            </Cell>
          </Sheet>
        ) : needsConfig && !config ? (
          <p className="pt-4 text-sm text-destructive">{error?.message ?? "Could not load your agent settings."}</p>
        ) : (
          <>
            <TabsContent value="lead" keepMounted className="space-y-6 pt-4">
              {config && (
                <>
                  <CollectStep config={config} onSaved={setSaved} />
                  <HotLeadStep config={config} onSaved={setSaved} />
                  <BusyCallsStep config={config} onSaved={setSaved} />
                </>
              )}
            </TabsContent>
            <TabsContent value="negotiation" className="pt-4">
              <NegotiationStep />
            </TabsContent>
            <TabsContent value="voice" keepMounted className="pt-4">
              <VoiceStep />
            </TabsContent>
            <TabsContent value="payments" keepMounted className="pt-4">
              {config && <PaymentsStep config={config} overrides={overrides} onSaved={setSaved} onOverridesChanged={setSavedOverrides} />}
            </TabsContent>
          </>
        )}
      </Tabs>
    </div>
  );
}

export default function AgentPage() {
  // useSearchParams needs a Suspense boundary (same pattern as the Leads page).
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <AgentPageContent />
    </Suspense>
  );
}
