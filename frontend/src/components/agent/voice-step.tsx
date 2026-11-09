"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { FieldRow, Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";
import type { AgentVoiceGender } from "@/lib/types";

/** How Mira sounds: greeting, personality, voice and the two spoken handoff lines. */
export function VoiceStep() {
  const { user, refreshUser } = useAuth();

  const [firstMessage, setFirstMessage] = useState(user?.agent_first_message ?? "");
  const [persona, setPersona] = useState(user?.agent_persona ?? "");
  const [escalationPhrase, setEscalationPhrase] = useState(user?.agent_escalation_phrase ?? "");
  const [handoffPhrase, setHandoffPhrase] = useState(user?.agent_handoff_phrase ?? "");
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
        agent_handoff_phrase: handoffPhrase || null,
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

  return (
    <form onSubmit={handleSavePersonalization}>
      <Sheet>
        <WideCell>
          <SheetHeading title="Voice agent personalization">Blank fields use Mira&apos;s default.</SheetHeading>
          <FieldRow
            label="Opening greeting"
            htmlFor="agent_first_message"
            hint="Placeholders: {host_name}, {property_name}, {city}, {guest_name}."
          >
            <DictationTextarea
              id="agent_first_message"
              placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
              value={firstMessage}
              onValueChange={setFirstMessage}
            />
          </FieldRow>
          <FieldRow label="Personality note" htmlFor="agent_persona">
            <DictationTextarea
              id="agent_persona"
              placeholder="e.g. Sound like a warm, chatty local host -- informal, never corporate."
              value={persona}
              onValueChange={setPersona}
            />
          </FieldRow>
          <FieldRow label="Voice">
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
          </FieldRow>
          <FieldRow
            label="Escalation phrase"
            htmlFor="agent_escalation_phrase"
            hint="Said when Mira takes your details for a later follow-up."
          >
            <DictationTextarea
              id="agent_escalation_phrase"
              placeholder="e.g. One moment, let me get my colleague on the line for you."
              value={escalationPhrase}
              onValueChange={setEscalationPhrase}
            />
          </FieldRow>
          <FieldRow
            label="Handoff phrase"
            htmlFor="agent_handoff_phrase"
            hint="Said when you take over a live call."
          >
            <DictationTextarea
              id="agent_handoff_phrase"
              placeholder="e.g. Hold on — the host is available now. I'm passing the call to them."
              value={handoffPhrase}
              onValueChange={setHandoffPhrase}
            />
          </FieldRow>
          <SheetFooter>
            <Button type="submit" variant="cta" disabled={savingPersonalization}>
              {savingPersonalization ? "Saving…" : "Save"}
            </Button>
          </SheetFooter>
        </WideCell>
      </Sheet>
    </form>
  );
}
