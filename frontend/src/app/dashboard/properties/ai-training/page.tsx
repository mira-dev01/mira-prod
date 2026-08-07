"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { AiTrainingSection } from "@/components/settings/ai-training-section";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";
import type { AgentVoiceGender } from "@/lib/types";

// Merged from the former "Voice AI" and "AI Training" tabs on the Settings
// page into a single full-screen page under Properties -- content and
// wiring unchanged from those tabs, just relocated.
export default function AiTrainingPage() {
  const { user, loading, refreshUser } = useAuth();

  const [firstMessage, setFirstMessage] = useState(user?.agent_first_message ?? "");
  const [persona, setPersona] = useState(user?.agent_persona ?? "");
  const [escalationPhrase, setEscalationPhrase] = useState(user?.agent_escalation_phrase ?? "");
  const [voiceGender, setVoiceGender] = useState<AgentVoiceGender>(user?.agent_voice_gender ?? "female");
  const [savingPersonalization, setSavingPersonalization] = useState(false);

  async function handleSavePersonalization(e: React.FormEvent) {
    e.preventDefault();
    setSavingPersonalization(true);
    try {
      await api.auth.updateMe({
        agent_first_message: firstMessage || null,
        agent_persona: persona || null,
        agent_escalation_phrase: escalationPhrase || null,
        agent_voice_gender: voiceGender,
      });
      await refreshUser();
      toast.success("Voice agent personalization saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save personalization");
    } finally {
      setSavingPersonalization(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-8">
        <div>
          <h1 className="page-title">AI Training</h1>
          <p className="text-sm text-muted-foreground">Voice agent personalization and negotiation policy</p>
        </div>
        <Card>
          <CardHeader>
            <Skeleton variant="text" className="w-1/2" />
          </CardHeader>
          <CardContent className="space-y-3">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} variant="text" />
            ))}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="page-title">AI Training</h1>
        <p className="text-sm text-muted-foreground">Voice agent personalization and negotiation policy</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Voice agent personalization</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-muted-foreground">
            Leave any field blank to use MIRA&apos;s default. The golden rules (never hallucinate
            pricing, always escalate when unsure, etc.) stay fixed regardless — these only change
            tone and wording.
          </p>
          <form onSubmit={handleSavePersonalization} className="space-y-6">
            <div className="space-y-2">
              <Label htmlFor="agent_first_message">Opening greeting</Label>
              <DictationTextarea
                id="agent_first_message"
                placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
                value={firstMessage}
                onValueChange={setFirstMessage}
              />
              <p className="text-xs text-muted-foreground">
                Placeholders: {"{host_name}"}, {"{property_name}"}, {"{city}"}, {"{guest_name}"} — any that
                don&apos;t apply to a given call are left blank automatically. Set during registration; edit
                it here any time.
              </p>
            </div>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="agent_persona">Personality note</Label>
                <DictationTextarea
                  id="agent_persona"
                  placeholder="e.g. Sound like a warm, chatty local host -- informal, never corporate."
                  value={persona}
                  onValueChange={setPersona}
                />
              </div>
              <div className="space-y-2">
                <Label>Voice</Label>
                <div className="flex gap-2">
                  {(["female", "male"] as const).map((gender) => (
                    <Button
                      key={gender}
                      type="button"
                      variant={voiceGender === gender ? "default" : "outline"}
                      size="sm"
                      aria-pressed={voiceGender === gender}
                      className={cn("capitalize", voiceGender === gender && "pointer-events-none")}
                      onClick={() => setVoiceGender(gender)}
                    >
                      {gender}
                    </Button>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">MIRA speaks in this voice on every call for this account.</p>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="agent_escalation_phrase">Escalation phrase</Label>
              <DictationTextarea
                id="agent_escalation_phrase"
                placeholder="e.g. One moment, let me get my colleague on the line for you."
                value={escalationPhrase}
                onValueChange={setEscalationPhrase}
              />
              <p className="text-xs text-muted-foreground">Said right before MIRA hands off to you.</p>
            </div>
            <Button type="submit" disabled={savingPersonalization}>
              Save personalization
            </Button>
          </form>
        </CardContent>
      </Card>

      <AiTrainingSection />
    </div>
  );
}
