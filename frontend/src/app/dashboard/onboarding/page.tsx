"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { MiraLogo } from "@/components/brand/mira-logo";
import { Button } from "@/components/ui/button";
import { Cell, CellTitle, Hint, Sheet, SubField } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { UseCasePicker } from "@/components/use-case-picker";
import { useAuth } from "@/lib/auth-context";
import { useOnboarding } from "@/lib/onboarding-context";
import { api, ApiError } from "@/lib/api";
import type { AirbnbHostStatus, UseCaseKey } from "@/lib/types";

const HOST_STATUS_OPTIONS: { value: AirbnbHostStatus; label: string }[] = [
  { value: "new_host", label: "New host" },
  { value: "individual_host", label: "Individual host" },
  { value: "superhost", label: "Superhost" },
  { value: "guest_favorite", label: "Guest Favorite" },
  { value: "professional_host", label: "Professional host" },
  { value: "not_on_airbnb", label: "Not on Airbnb" },
  { value: "prefer_not_to_say", label: "Prefer not to say" },
];

const STEPS = ["What matters to you", "Your details", "Hosting profile", "Voice agent intro"] as const;

// The wizard keeps everything in memory until the last step submits it, so a reload, a dropped connection or a
// sign-in bounce used to send the host back to step one with every field empty. The in-progress answers are kept
// in this browser (per account) and cleared once the profile is saved. Nothing secret is stored here.
type OnboardingDraft = {
  step: number;
  interests: UseCaseKey[];
  name: string;
  phone: string;
  businessName: string;
  businessPhone: string;
  hostStatus: AirbnbHostStatus | "";
  propertyCount: string;
  firstMessage: string;
};

const draftKeyFor = (userId: string) => `mira:onboarding-draft:${userId}`;

function readDraft(userId: string | undefined): Partial<OnboardingDraft> {
  if (!userId || typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(draftKeyFor(userId));
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as Partial<OnboardingDraft>) : {};
  } catch {
    return {}; // storage blocked or the draft is corrupt: start fresh
  }
}

function clearDraft(userId: string | undefined) {
  if (!userId || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(draftKeyFor(userId));
  } catch {
    // nothing to clear if storage is unavailable
  }
}

// Post-signup onboarding: collects the business/Airbnb-import fields Clerk's
// own sign-up form doesn't know about (see app/api/v1/auth.py onboard_host).
// Only reachable once Clerk auth has already succeeded -- identity (email,
// password) is Clerk's job now, not this form's.
export default function OnboardingPage() {
  const { user, refreshUser } = useAuth();
  const router = useRouter();
  // The dashboard's gate (app/dashboard/layout.tsx) lets a host in only once the
  // server-side onboarding record is "completed". Finishing this form must
  // complete that record AND update the shared state before navigating, or the
  // gate still sees an unfinished host and sends them straight back to step one.
  const { state: onboardingState, loading: onboardingLoading, setState: setOnboardingState } = useOnboarding();
  const [submitting, setSubmitting] = useState(false);
  // Answers saved by an earlier, interrupted visit (see OnboardingDraft above).
  const [draft] = useState(() => readDraft(user?.id));
  const [step, setStep] = useState(() =>
    Number.isInteger(draft.step) ? Math.min(Math.max(draft.step as number, 0), STEPS.length - 1) : 0
  );
  // Set if finishing an already-saved profile failed: show the form rather than
  // bounce between here and the dashboard gate.
  const [finishFailed, setFinishFailed] = useState(false);

  // This wizard is for brand-new hosts only. Anyone who already has a profile
  // (onboarding finished, or the profile step saved a name + business number
  // but the final "complete" call never landed) is sent to the dashboard --
  // never shown the form again. If the state can't be loaded we also leave:
  // the dashboard gate fails open the same way, and an unknown host must not
  // be dropped into a form that would overwrite their saved profile.
  const hasProfile = Boolean((user?.name ?? "").trim() && user?.lead_exophone);
  // (Not while this form is itself submitting: saving the profile makes hasProfile
  // true a moment before handleSubmit finishes the record and navigates.)
  const alreadySetUp = !submitting && !finishFailed && (onboardingState?.status === "completed" || (onboardingState !== null && hasProfile));
  const checkFailed = !onboardingLoading && onboardingState === null;
  const finishingRef = useRef(false);
  const draftDoneRef = useRef(false); // set once the profile is saved: the draft is then cleared and not rewritten

  useEffect(() => {
    if (checkFailed) {
      router.replace("/dashboard");
      return;
    }
    if (!alreadySetUp || finishingRef.current) return;
    finishingRef.current = true;
    draftDoneRef.current = true;
    clearDraft(user?.id);
    (async () => {
      try {
        // A profile saved without the completion record: finish it, so the
        // dashboard gate lets the host in instead of sending them back here.
        if (onboardingState?.status !== "completed") setOnboardingState(await api.onboarding.complete());
        router.replace("/dashboard");
      } catch {
        finishingRef.current = false;
        setFinishFailed(true);
      }
    })();
  }, [alreadySetUp, checkFailed, onboardingState?.status, router, setOnboardingState, user?.id]);

  const [name, setName] = useState(draft.name ?? "");
  const [businessName, setBusinessName] = useState(draft.businessName ?? "");
  const [phone, setPhone] = useState(draft.phone ?? "");

  const [businessPhone, setBusinessPhone] = useState(draft.businessPhone ?? "");
  const [hostStatus, setHostStatus] = useState<AirbnbHostStatus | "">(draft.hostStatus ?? "");
  const [propertyCount, setPropertyCount] = useState(draft.propertyCount ?? "");

  const [firstMessage, setFirstMessage] = useState(draft.firstMessage ?? "");
  const [isRecording, setIsRecording] = useState(false);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);


  // Step 0 -- dashboard/onboarding personalization (see
  // frontend/src/lib/use-cases.ts). Multi-select, nothing exclusive;
  // skipping leaves this []. Purely a UI-emphasis signal downstream
  // (sidebar-nav.tsx, dashboard/page.tsx) -- never gates a feature.
  const [interests, setInterests] = useState<UseCaseKey[]>(Array.isArray(draft.interests) ? draft.interests : []);

  function toggleInterest(key: UseCaseKey) {
    setInterests((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  // Keep the draft current on every change. Stops once the profile is saved so it can't be written back.
  useEffect(() => {
    if (!user?.id || draftDoneRef.current) return;
    const next: OnboardingDraft = {
      step, interests, name, phone, businessName, businessPhone, hostStatus, propertyCount, firstMessage,
    };
    try {
      window.localStorage.setItem(draftKeyFor(user.id), JSON.stringify(next));
    } catch {
      // storage full or blocked: the wizard still works, it just won't resume
    }
  }, [user?.id, step, interests, name, phone, businessName, businessPhone, hostStatus, propertyCount, firstMessage]);

  function goNext(e: React.FormEvent) {
    e.preventDefault();
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  function goBack() {
    setStep((s) => Math.max(s - 1, 0));
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setRecordedUrl(URL.createObjectURL(blob));
        stream.getTracks().forEach((track) => track.stop());
        setTranscribing(true);
        try {
          const { text } = await api.auth.transcribeRegistrationIntro(blob);
          setFirstMessage(text);
        } catch (err) {
          toast.error(err instanceof ApiError ? err.message : "Could not transcribe recording -- type it instead");
        } finally {
          setTranscribing(false);
        }
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      toast.error("Could not access the microphone -- check browser permissions");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.auth.onboard({
        name,
        phone: phone || undefined,
        business_name: businessName || undefined,
        business_phone: businessPhone,
        airbnb_host_status: hostStatus || undefined,
        property_count_estimate: propertyCount ? Number(propertyCount) : undefined,
        agent_first_message: firstMessage || undefined,
        interests,
      });
      // Saved: the draft has done its job (and must not be written back by the effect above).
      draftDoneRef.current = true;
      clearDraft(user?.id);
      await refreshUser();
      setOnboardingState(await api.onboarding.complete());
      router.push("/dashboard");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save onboarding details");
    } finally {
      setSubmitting(false);
    }
  }

  // Nothing of the form is shown until we know this really is a new host.
  if (onboardingState === null || alreadySetUp) {
    return <div className="flex flex-1 items-center justify-center p-4 text-sm text-muted-foreground">Loading…</div>;
  }

  const nav = (back: (() => void) | null, next: React.ReactNode) => (
    <div className="flex gap-2 pt-2">
      {back && (
        <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={back}>
          Back
        </Button>
      )}
      {next}
    </div>
  );

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Sheet className="w-full max-w-lg">
        <Cell className="gap-5">
          <MiraLogo className="h-9 self-start text-foreground" />
          <div className="space-y-2">
            <div className="flex gap-1" role="progressbar" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={step + 1}>
              {STEPS.map((label, i) => (
                <div key={label} className={`h-1 flex-1 ${i <= step ? "bg-foreground" : "bg-border"}`} />
              ))}
            </div>
            <CellTitle>
              Step {step + 1} of {STEPS.length} · {STEPS[step]}
            </CellTitle>
          </div>
        </Cell>

        <Cell className="gap-0">
          {step === 0 && (
            <form onSubmit={goNext} className="space-y-4">
              <Hint>Pick any. This only changes what we show first.</Hint>
              <UseCasePicker selected={interests} onToggle={toggleInterest} />
              {nav(
                null,
                <>
                  <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={() => setStep(1)}>
                    Skip for now
                  </Button>
                  <Button type="submit" variant="cta" className="min-w-0 flex-1">
                    Continue
                  </Button>
                </>
              )}
            </form>
          )}

          {step === 1 && (
            <form onSubmit={goNext} className="space-y-4">
              <SubField label="Your name" htmlFor="ob-name">
                <Input id="ob-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </SubField>
              <SubField label="Business name (optional)" htmlFor="ob-business-name">
                <Input id="ob-business-name" value={businessName} onChange={(e) => setBusinessName(e.target.value)} />
              </SubField>
              <SubField label="Personal phone (optional)" htmlFor="ob-phone">
                <Input id="ob-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
              </SubField>
              {nav(
                goBack,
                <Button type="submit" variant="cta" className="min-w-0 flex-1">
                  Continue
                </Button>
              )}
            </form>
          )}

          {step === 2 && (
            <form onSubmit={goNext} className="space-y-4">
              <SubField label="Business number" htmlFor="ob-business-phone">
                <Input
                  id="ob-business-phone"
                  required
                  placeholder="+91XXXXXXXXXX"
                  value={businessPhone}
                  onChange={(e) => setBusinessPhone(e.target.value)}
                />
                <Hint>Mira answers guest calls on this number.</Hint>
              </SubField>
              <SubField label="Airbnb host status (optional)" htmlFor="ob-host-status">
                <Select value={hostStatus} onValueChange={(v) => setHostStatus((v as AirbnbHostStatus) || "")}>
                  <SelectTrigger id="ob-host-status" className="w-full">
                    <SelectValue placeholder="Select your host status">
                      {HOST_STATUS_OPTIONS.find((opt) => opt.value === hostStatus)?.label}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {HOST_STATUS_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </SubField>
              <SubField label="Approx. number of properties (optional)" htmlFor="ob-property-count">
                <Input
                  id="ob-property-count"
                  type="number"
                  min={1}
                  value={propertyCount}
                  onChange={(e) => setPropertyCount(e.target.value)}
                />
              </SubField>
              {nav(
                goBack,
                <Button type="submit" variant="cta" className="min-w-0 flex-1">
                  Continue
                </Button>
              )}
            </form>
          )}

          {step === 3 && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <SubField label="Your voice agent's intro" htmlFor="ob-first-message">
                <Textarea
                  id="ob-first-message"
                  placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
                  value={firstMessage}
                  onChange={(e) => setFirstMessage(e.target.value)}
                  disabled={transcribing}
                />
                <Hint>Optional. Blank uses Mira&apos;s default greeting.</Hint>
              </SubField>

              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-3">
                  {!isRecording ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      disabled={transcribing}
                      onClick={startRecording}
                    >
                      {recordedUrl ? "Re-record" : "Or record it instead"}
                    </Button>
                  ) : (
                    <Button type="button" variant="destructive" size="sm" className="shrink-0" onClick={stopRecording}>
                      Stop recording
                    </Button>
                  )}
                  {recordedUrl && <audio controls src={recordedUrl} className="h-8 min-w-0 flex-1" />}
                </div>
                {(transcribing || recordedUrl) && (
                  <Hint>{transcribing ? "Transcribing your recording…" : "Transcribed above. Edit the text if needed."}</Hint>
                )}
              </div>

              {nav(
                goBack,
                <Button type="submit" variant="cta" className="min-w-0 flex-1" disabled={transcribing || submitting}>
                  Finish setup
                </Button>
              )}
            </form>
          )}
        </Cell>
      </Sheet>
    </div>
  );
}
