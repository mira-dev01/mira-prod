"use client";

<<<<<<< HEAD
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth-context";
import { PENDING_IMPORT_KEY } from "@/lib/auth-context";
import { api, ApiError } from "@/lib/api";
import type { AirbnbHostStatus } from "@/lib/types";
=======
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Check, CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { CapabilityActions, CapabilityStateChip } from "@/components/capability-state-chip";
import { InfoTip } from "@/components/ui/info-tip";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useOnboarding } from "@/lib/onboarding-context";
import { cn } from "@/lib/utils";
import type {
  AirbnbHostStatus,
  CapabilityStatus,
  HostCapabilities,
  OnboardingState,
  OnboardingStep,
  UserUpdate,
} from "@/lib/types";
>>>>>>> 368377a (20th October)

const HOST_STATUS_OPTIONS: { value: AirbnbHostStatus; label: string }[] = [
  { value: "new_host", label: "New host" },
  { value: "individual_host", label: "Individual host" },
  { value: "superhost", label: "Superhost" },
  { value: "guest_favorite", label: "Guest Favorite" },
  { value: "professional_host", label: "Professional host" },
  { value: "prefer_not_to_say", label: "Prefer not to say" },
];

<<<<<<< HEAD
const STEPS = ["Your details", "Hosting profile", "Voice agent intro", "First property"] as const;

// Post-signup onboarding: collects the business/Airbnb-import fields Clerk's
// own sign-up form doesn't know about (see app/api/v1/auth.py onboard_host).
// Only reachable once Clerk auth has already succeeded -- identity (email,
// password) is Clerk's job now, not this form's.
export default function OnboardingPage() {
  const { refreshUser } = useAuth();
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [step, setStep] = useState(0);

  const [name, setName] = useState("");
  const [businessName, setBusinessName] = useState("");
  const [phone, setPhone] = useState("");

  const [businessPhone, setBusinessPhone] = useState("");
  const [hostStatus, setHostStatus] = useState<AirbnbHostStatus | "">("");
  const [propertyCount, setPropertyCount] = useState("");

  const [firstMessage, setFirstMessage] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const [airbnbUrl, setAirbnbUrl] = useState("");
  const [icalUrl, setIcalUrl] = useState("");

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
=======
const STEPS: { id: OnboardingStep; label: string }[] = [
  { id: "profile", label: "Business profile" },
  { id: "capabilities", label: "Choose features" },
  { id: "setup", label: "Set up" },
  { id: "review", label: "Review" },
];

function toStep(value: string | undefined): OnboardingStep {
  return STEPS.some((s) => s.id === value) ? (value as OnboardingStep) : "profile";
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// Post-signup onboarding. Clerk owns identity; this collects the business
// profile (saved through the existing PATCH /auth/me), the capabilities the
// host wants, and only the setup those capabilities need. Every step is
// saved on the server (backend app/services/onboarding_service.py), so a
// refresh, closed tab or sign-in elsewhere resumes at the same step with
// the same selections -- nothing here lives only in component state.
export default function OnboardingPage() {
  const router = useRouter();
  const { state, error: stateError, refetch: refetchState, setState } = useOnboarding();
  const {
    data: capabilities,
    error: capabilitiesError,
    refetch: refetchCapabilities,
    setData: setCapabilities,
  } = useAsync(() => api.capabilities.list(), []);

  const [step, setStep] = useState<OnboardingStep>(() => toStep(state?.current_step));

  useEffect(() => {
    if (state?.status === "completed") router.replace("/dashboard");
  }, [state?.status, router]);

  async function persist(update: Parameters<typeof api.onboarding.save>[0]): Promise<OnboardingState> {
    const next = await api.onboarding.save(update);
    setState(next);
    return next;
  }

  function goTo(target: OnboardingStep) {
    setStep(target);
    // Best-effort: the step itself is already saved by the forward action;
    // this only makes a refresh land on the step being viewed.
    persist({ current_step: target }).catch(() => undefined);
  }

  if (stateError && !state) {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground">We couldn&apos;t load your setup progress.</p>
        <Button className="mt-4" onClick={() => void refetchState()}>
          Try again
        </Button>
      </Shell>
    );
  }

  const stepIndex = STEPS.findIndex((s) => s.id === step);

  return (
    <Shell wide={step !== "profile"}>
      <Stepper current={stepIndex} />
      {step === "profile" && <ProfileStep onDone={() => setStep("capabilities")} persist={persist} />}
      {step !== "profile" && !capabilities && !capabilitiesError && <StepSkeleton />}
      {step !== "profile" && capabilitiesError && !capabilities && (
        <div className="space-y-3 pt-2">
          <p className="text-sm text-muted-foreground">We couldn&apos;t load the feature list.</p>
          <Button variant="outline" onClick={refetchCapabilities}>
            Try again
          </Button>
        </div>
      )}
      {step === "capabilities" && capabilities && state && (
        <CapabilitiesStep
          capabilities={capabilities}
          initialSelection={state.selected_capabilities}
          persist={persist}
          onBack={() => goTo("profile")}
          onDone={() => {
            refetchCapabilities();
            setStep("setup");
          }}
        />
      )}
      {step === "setup" && capabilities && state && (
        <SetupStep
          capabilities={capabilities}
          onboarding={state}
          persist={persist}
          onBack={() => goTo("capabilities")}
          onDone={(next) => {
            setCapabilities(next);
            setStep("review");
          }}
        />
      )}
      {step === "review" && capabilities && state && (
        <ReviewStep
          capabilities={capabilities}
          onboarding={state}
          onBack={() => goTo("setup")}
          onRefreshCapabilities={refetchCapabilities}
          onGoToProfile={() => goTo("profile")}
        />
      )}
    </Shell>
  );
}

function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="flex min-h-full items-start justify-center p-4 md:items-center md:py-10">
      <Card className={cn("w-full", wide ? "max-w-3xl" : "max-w-md")}>
        <CardHeader>
          <CardTitle className="brand-logo text-3xl">mira</CardTitle>
          <CardDescription>Set up your host workspace</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </div>
  );
}

function Stepper({ current }: { current: number }) {
  return (
    <>
      <div className="flex items-center justify-center gap-2 pb-2">
        {STEPS.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2">
            <div
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-full text-xs",
                i === current
                  ? "bg-primary text-primary-foreground"
                  : i < current
                    ? "bg-primary/20 text-primary"
                    : "bg-muted text-muted-foreground"
              )}
            >
              {i + 1}
            </div>
            {i < STEPS.length - 1 && <div className="h-px w-4 bg-border" />}
          </div>
        ))}
      </div>
      <p className="pb-2 text-center text-xs text-muted-foreground">{STEPS[current]?.label}</p>
    </>
  );
}

function StepSkeleton() {
  return (
    <div className="space-y-3 pt-2">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

function StepButtons({
  onBack,
  submitLabel,
  submitting,
  disabled,
}: {
  onBack?: () => void;
  submitLabel: string;
  submitting: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="flex gap-2 pt-2">
      {onBack && (
        <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={onBack} disabled={submitting}>
          Back
        </Button>
      )}
      <Button type="submit" className="min-w-0 flex-1" disabled={submitting || disabled}>
        {submitting ? "Saving…" : submitLabel}
      </Button>
    </div>
  );
}

// ── Step 1: business profile ─────────────────────────────────────────
// Same User fields the old onboarding and Settings already use -- nothing
// duplicated, just saved through PATCH /auth/me.
function ProfileStep({
  onDone,
  persist,
}: {
  onDone: () => void;
  persist: (u: Parameters<typeof api.onboarding.save>[0]) => Promise<OnboardingState>;
}) {
  const { user, setUserData } = useAuth();
  const [name, setName] = useState(user?.name ?? "");
  const [businessName, setBusinessName] = useState(user?.business_name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [hostStatus, setHostStatus] = useState<AirbnbHostStatus | "">(user?.airbnb_host_status ?? "");
  const [propertyCount, setPropertyCount] = useState(user?.property_count_estimate?.toString() ?? "");
  const [submitting, setSubmitting] = useState(false);
>>>>>>> 368377a (20th October)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
<<<<<<< HEAD
      const { snapshot_id, import_error } = await api.auth.onboard({
        name,
        phone: phone || undefined,
        business_name: businessName || undefined,
        business_phone: businessPhone,
        airbnb_host_status: hostStatus || undefined,
        property_count_estimate: propertyCount ? Number(propertyCount) : undefined,
        agent_first_message: firstMessage || undefined,
        airbnb_url: airbnbUrl,
        ical_url: icalUrl || undefined,
      });
      await refreshUser();
      if (snapshot_id) {
        window.sessionStorage.setItem(PENDING_IMPORT_KEY, JSON.stringify({ snapshotId: snapshot_id, icalUrl: icalUrl || null }));
      } else if (import_error) {
        window.sessionStorage.setItem(PENDING_IMPORT_KEY, JSON.stringify({ error: import_error }));
      }
      router.push("/dashboard");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save onboarding details");
=======
      const updated = await api.auth.updateMe({
        name: name.trim(),
        business_name: businessName.trim() || null,
        phone: phone.trim() || null,
        airbnb_host_status: hostStatus || null,
        property_count_estimate: propertyCount ? Number(propertyCount) : null,
      });
      setUserData(updated);
      await persist({ current_step: "capabilities", completed_steps: ["profile"] });
      onDone();
    } catch (err) {
      toast.error(errorMessage(err, "Could not save your profile"));
>>>>>>> 368377a (20th October)
    } finally {
      setSubmitting(false);
    }
  }

  return (
<<<<<<< HEAD
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="brand-logo text-3xl">mira</CardTitle>
          <CardDescription>Set up your host profile</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center gap-2 pb-2">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-2">
                <div
                  className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                    i === step
                      ? "bg-primary text-primary-foreground"
                      : i < step
                        ? "bg-primary/20 text-primary"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {i + 1}
                </div>
                {i < STEPS.length - 1 && <div className="h-px w-4 bg-border" />}
              </div>
            ))}
          </div>
          <p className="pb-2 text-center text-xs text-muted-foreground">{STEPS[step]}</p>

          {step === 0 && (
            <form onSubmit={goNext} className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label htmlFor="ob-name">Your name</Label>
                <Input id="ob-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ob-business-name">Business name (optional)</Label>
                <Input id="ob-business-name" value={businessName} onChange={(e) => setBusinessName(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ob-phone">Personal phone (optional)</Label>
                <Input id="ob-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
              </div>
              <Button type="submit" className="w-full">
                Continue
              </Button>
            </form>
          )}

          {step === 1 && (
            <form onSubmit={goNext} className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label htmlFor="ob-business-phone">Business number</Label>
                <Input
                  id="ob-business-phone"
                  required
                  placeholder="+91XXXXXXXXXX"
                  value={businessPhone}
                  onChange={(e) => setBusinessPhone(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  The number your voice agent will run on for guest calls.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="ob-host-status">Airbnb host status (optional)</Label>
                <Select value={hostStatus} onValueChange={(v) => setHostStatus((v as AirbnbHostStatus) || "")}>
                  <SelectTrigger id="ob-host-status">
                    <SelectValue placeholder="Select your host status" />
                  </SelectTrigger>
                  <SelectContent>
                    {HOST_STATUS_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="ob-property-count">Approx. number of properties (optional)</Label>
                <Input
                  id="ob-property-count"
                  type="number"
                  min={1}
                  value={propertyCount}
                  onChange={(e) => setPropertyCount(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goBack}>
                  Back
                </Button>
                <Button type="submit" className="min-w-0 flex-1">
                  Continue
                </Button>
              </div>
            </form>
          )}

          {step === 2 && (
            <form onSubmit={goNext} className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label htmlFor="ob-first-message">Add your voice agent&apos;s intro</Label>
                <Textarea
                  id="ob-first-message"
                  placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
                  value={firstMessage}
                  onChange={(e) => setFirstMessage(e.target.value)}
                  disabled={transcribing}
                />
                <p className="text-xs text-muted-foreground">
                  Optional — leave blank to use MIRA&apos;s default greeting. Placeholders: {"{host_name}"},{" "}
                  {"{property_name}"}, {"{city}"}, {"{guest_name}"} — any that don&apos;t apply to a given call are
                  left blank automatically.
                </p>
              </div>

              <div className="space-y-2 rounded-md border p-3">
                <Label>Or record it instead</Label>
                <div className="flex items-center gap-3">
                  {!isRecording ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      disabled={transcribing}
                      onClick={startRecording}
                    >
                      {recordedUrl ? "Re-record" : "Start recording"}
                    </Button>
                  ) : (
                    <Button type="button" variant="destructive" size="sm" className="shrink-0" onClick={stopRecording}>
                      Stop recording
                    </Button>
                  )}
                  {recordedUrl && <audio controls src={recordedUrl} className="h-8 min-w-0 flex-1" />}
                </div>
                <p className="text-xs text-muted-foreground">
                  {transcribing
                    ? "Transcribing your recording…"
                    : recordedUrl
                      ? "Transcribed below — edit the text above if needed."
                      : "Speak your greeting and it'll be transcribed into the field above."}
                </p>
              </div>

              <div className="flex gap-2">
                <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goBack}>
                  Back
                </Button>
                <Button type="submit" className="min-w-0 flex-1" disabled={transcribing}>
                  Continue
                </Button>
              </div>
            </form>
          )}

          {step === 3 && (
            <form onSubmit={handleSubmit} className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label htmlFor="ob-airbnb-url">Airbnb listing URL</Label>
                <Input
                  id="ob-airbnb-url"
                  required
                  placeholder="https://www.airbnb.co.in/rooms/12345678"
                  value={airbnbUrl}
                  onChange={(e) => setAirbnbUrl(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  We&apos;ll import this property automatically. Add the rest later from the Properties page.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="ob-ical-url">iCal link (optional)</Label>
                <Input id="ob-ical-url" value={icalUrl} onChange={(e) => setIcalUrl(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goBack}>
                  Back
                </Button>
                <Button type="submit" className="min-w-0 flex-1" disabled={submitting}>
                  Finish setup
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
=======
    <form onSubmit={handleSubmit} className="space-y-4 pt-2">
      <div className="space-y-2">
        <Label htmlFor="ob-name">Your name</Label>
        <Input id="ob-name" required value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="ob-business-name">Business name (optional)</Label>
        <Input id="ob-business-name" value={businessName} onChange={(e) => setBusinessName(e.target.value)} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="ob-phone">Your phone (optional)</Label>
        <Input id="ob-phone" placeholder="+9198XXXXXXXX" value={phone} onChange={(e) => setPhone(e.target.value)} />
        <p className="text-xs text-muted-foreground">
          Where Mira sends urgent alerts and transfers guests who need you.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="ob-host-status">Airbnb host status (optional)</Label>
        <Select value={hostStatus} onValueChange={(v) => setHostStatus((v as AirbnbHostStatus) || "")}>
          <SelectTrigger id="ob-host-status">
            <SelectValue placeholder="Select your host status" />
          </SelectTrigger>
          <SelectContent>
            {HOST_STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="ob-property-count">Approx. number of properties (optional)</Label>
        <Input
          id="ob-property-count"
          type="number"
          min={1}
          value={propertyCount}
          onChange={(e) => setPropertyCount(e.target.value)}
        />
      </div>
      <StepButtons submitLabel="Continue" submitting={submitting} />
    </form>
  );
}

// ── Step 2: choose capabilities ──────────────────────────────────────
function CapabilitiesStep({
  capabilities,
  initialSelection,
  persist,
  onBack,
  onDone,
}: {
  capabilities: HostCapabilities;
  initialSelection: string[];
  persist: (u: Parameters<typeof api.onboarding.save>[0]) => Promise<OnboardingState>;
  onBack: () => void;
  onDone: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set(initialSelection));
  const [submitting, setSubmitting] = useState(false);
  const byId = useMemo(() => new Map(capabilities.capabilities.map((c) => [c.id, c])), [capabilities]);

  function toggle(cap: CapabilityStatus, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(cap.id);
      else next.delete(cap.id);
      return next;
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      // Unavailable capabilities render unchecked -- never submit them either.
      const selectable = capabilities.capabilities
        .filter((c) => c.selectable && c.state !== "unavailable")
        .map((c) => c.id);
      const next = await persist({
        selected_capabilities: selectable.filter((id) => selected.has(id)),
        current_step: "setup",
        completed_steps: ["profile", "capabilities"],
      });
      next.selection_notes.forEach((note) => toast.warning(note));
      onDone();
    } catch (err) {
      toast.error(errorMessage(err, "Could not save your feature choices"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 pt-2">
      <p className="text-sm text-muted-foreground">
        Pick what you&apos;d like Mira to handle. You can change this any time in Settings → Features &amp; modules.
      </p>
      {capabilities.groups.map((group) => {
        const groupCaps = capabilities.capabilities.filter((c) => c.group === group.id);
        const choices = groupCaps.filter((c) => c.selectable);
        const included = groupCaps.filter((c) => !c.selectable);
        return (
          <section key={group.id} className="space-y-2">
            <h2 className="text-sm font-medium">{group.name}</h2>
            {choices.length > 0 && (
              <div className="grid gap-2 sm:grid-cols-2">
                {choices.map((cap) => {
                  const unavailable = cap.state === "unavailable";
                  const checked = selected.has(cap.id) && !unavailable;
                  const prerequisites = cap.requirements.filter((r) => r.hard);
                  return (
                    <label
                      key={cap.id}
                      className={cn(
                        "flex cursor-pointer gap-3 rounded-lg border bg-card p-3 transition-colors",
                        checked ? "border-primary/60 bg-accent/40" : "hover:bg-accent/30",
                        unavailable && "cursor-not-allowed opacity-60"
                      )}
                    >
                      <Checkbox
                        className="mt-0.5"
                        checked={checked}
                        disabled={unavailable}
                        onCheckedChange={(v) => toggle(cap, v === true)}
                      />
                      <span className="min-w-0 space-y-1">
                        <span className="flex items-center gap-1 text-sm font-medium">
                          {cap.name}
                          <InfoTip label={`About ${cap.name}`}>
                            <p className="text-foreground">{cap.description}</p>
                            <p className="text-muted-foreground">{cap.benefit}</p>
                          </InfoTip>
                        </span>
                        {unavailable && cap.enable_blocked_reason ? (
                          <span className="block text-xs text-muted-foreground">{cap.enable_blocked_reason}</span>
                        ) : (
                          prerequisites.length > 0 && (
                            <span className="block text-xs text-muted-foreground">
                              Needs: {prerequisites.map((r) => r.label.toLowerCase()).join("; ")}
                            </span>
                          )
                        )}
                        {cap.live_in_backend && !checked && (
                          <span className="block text-xs text-muted-foreground">Already active on your account.</span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            {included.length > 0 && (
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {included.map((cap) => (
                  <li key={cap.id} className="flex items-center gap-1" title={cap.description}>
                    <Check className="size-3 text-[var(--status-live)]" />
                    {cap.name} <span className="text-muted-foreground/70">· included</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
      {byId.size === 0 && <p className="text-sm text-muted-foreground">No features to choose from.</p>}
      <StepButtons onBack={onBack} submitLabel="Continue" submitting={submitting} />
    </form>
  );
}

// ── Step 3: set up only what was selected ────────────────────────────
function SetupStep({
  capabilities,
  onboarding,
  persist,
  onBack,
  onDone,
}: {
  capabilities: HostCapabilities;
  onboarding: OnboardingState;
  persist: (u: Parameters<typeof api.onboarding.save>[0]) => Promise<OnboardingState>;
  onBack: () => void;
  onDone: (capabilities: HostCapabilities) => void;
}) {
  const { user, setUserData } = useAuth();
  const { setState } = useOnboarding();
  const selected = new Set(onboarding.selected_capabilities);
  const byId = new Map(capabilities.capabilities.map((c) => [c.id, c]));
  const importAvailable = byId.get("airbnb_import")?.available ?? false;
  const wantsLeadAgent = selected.has("lead_agent");
  const wantsCalendar = selected.has("calendar_sync");
  const wantsLivePricing = selected.has("live_airbnb_pricing");
  const wantsNegotiation = selected.has("negotiation");
  const wantsTechnicians = selected.has("technician_dispatch");

  const existingImport = onboarding.first_property;
  const importLocked = existingImport?.status === "importing" || existingImport?.status === "completed";

  const [airbnbUrl, setAirbnbUrl] = useState(existingImport?.airbnb_url ?? "");
  const [icalUrl, setIcalUrl] = useState(existingImport?.ical_url ?? "");
  const [livePricing, setLivePricing] = useState(existingImport?.live_pricing ?? wantsLivePricing);
  const [leadNumber, setLeadNumber] = useState(user?.lead_exophone ?? "");
  const [intro, setIntro] = useState(user?.agent_first_message ?? "");
  const [maxDiscount, setMaxDiscount] = useState(user?.max_discount_percent_override?.toString() ?? "");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      // 1. Host fields with an existing home -- one PATCH /auth/me, only
      //    what changed.
      const updates: UserUpdate = {};
      if (wantsLeadAgent && leadNumber.trim() !== (user?.lead_exophone ?? "")) {
        updates.lead_exophone = leadNumber.trim() || null;
      }
      if (intro.trim() !== (user?.agent_first_message ?? "")) updates.agent_first_message = intro.trim() || null;
      if (wantsNegotiation) {
        const current = user?.max_discount_percent_override?.toString() ?? "";
        if (maxDiscount.trim() !== current) {
          updates.max_discount_percent_override = maxDiscount.trim() ? Number(maxDiscount) : null;
        }
      }
      if (Object.keys(updates).length > 0) setUserData(await api.auth.updateMe(updates));

      // 2. First property: the server runs the import to completion, so
      //    it's safe to move on while it's still going. Re-submitting the
      //    same URL never starts a second scrape (server-side idempotent).
      const url = airbnbUrl.trim();
      if (url && importAvailable) {
        const sameAsExisting = existingImport && importLocked;
        const changedExtras =
          sameAsExisting &&
          ((icalUrl.trim() || null) !== existingImport.ical_url || livePricing !== existingImport.live_pricing);
        if (!sameAsExisting || changedExtras || existingImport.status === "failed") {
          setState(
            await api.onboarding.startFirstPropertyImport({
              airbnb_url: url,
              ical_url: wantsCalendar ? icalUrl.trim() || null : null,
              live_pricing: wantsLivePricing && livePricing,
            })
          );
        }
      }

      // 3. Anything selected but skipped here is marked "set up later" so
      //    Settings can point back to it.
      if (wantsTechnicians) await api.capabilities.update("technician_dispatch", { setup_state: "deferred" });
      if (wantsLeadAgent && !leadNumber.trim()) await api.capabilities.update("lead_agent", { setup_state: "deferred" });

      await persist({ current_step: "review", completed_steps: ["profile", "capabilities", "setup"] });
      onDone(await api.capabilities.list());
    } catch (err) {
      toast.error(errorMessage(err, "Could not save your setup"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 pt-2">
      <p className="text-sm text-muted-foreground">
        Everything here is optional — skip anything and finish it later from the dashboard.
      </p>

      <SetupSection title="Your first property">
        {!importAvailable ? (
          <p className="text-sm text-muted-foreground">
            Airbnb import isn&apos;t available on this workspace yet. Add your property from the Properties page once
            you&apos;re in.
          </p>
        ) : (
          <>
            {existingImport && <ImportStatusLine record={existingImport} />}
            <div className="space-y-2">
              <Label htmlFor="ob-airbnb-url">Airbnb listing URL</Label>
              <Input
                id="ob-airbnb-url"
                placeholder="https://www.airbnb.co.in/rooms/12345678"
                value={airbnbUrl}
                disabled={importLocked}
                onChange={(e) => setAirbnbUrl(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                We&apos;ll import details, photos and FAQs. Add more properties later from Properties.
              </p>
            </div>
            {wantsCalendar && (
              <div className="space-y-2">
                <Label htmlFor="ob-ical-url">iCal link (optional)</Label>
                <Input id="ob-ical-url" value={icalUrl} onChange={(e) => setIcalUrl(e.target.value)} />
                <p className="text-xs text-muted-foreground">
                  Keeps Mira from offering dates already booked on Airbnb.
                </p>
              </div>
            )}
            {wantsLivePricing && (
              <label className="flex items-start gap-2 text-sm">
                <Checkbox className="mt-0.5" checked={livePricing} onCheckedChange={(v) => setLivePricing(v === true)} />
                <span>Quote this property at its live Airbnb price</span>
              </label>
            )}
          </>
        )}
      </SetupSection>

      {wantsLeadAgent && (
        <SetupSection title="Call intake number">
          <div className="space-y-2">
            <Label htmlFor="ob-lead-number">Number guests call for booking enquiries</Label>
            <Input
              id="ob-lead-number"
              placeholder="+9180XXXXXXXX"
              value={leadNumber}
              onChange={(e) => setLeadNumber(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Calls to this number run the Lead Agent across all your properties.
            </p>
          </div>
        </SetupSection>
      )}

      <SetupSection title="Voice agent intro">
        <div className="space-y-2">
          <Label htmlFor="ob-intro">What Mira says when she answers (optional)</Label>
          <DictationTextarea
            id="ob-intro"
            placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
            value={intro}
            onValueChange={setIntro}
          />
          <p className="text-xs text-muted-foreground">
            Leave blank for Mira&apos;s default. Placeholders: {"{host_name}"}, {"{property_name}"}, {"{city}"},{" "}
            {"{guest_name}"}. Fine-tune the rest in AI Training.
          </p>
        </div>
      </SetupSection>

      {wantsNegotiation && (
        <SetupSection title="Discount limit">
          <div className="space-y-2">
            <Label htmlFor="ob-max-discount">Maximum discount Mira may offer (%)</Label>
            <Input
              id="ob-max-discount"
              type="number"
              min={0}
              max={100}
              step="0.5"
              placeholder="Mira's default"
              value={maxDiscount}
              onChange={(e) => setMaxDiscount(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Describe your full discount policy later in AI Training.
            </p>
          </div>
        </SetupSection>
      )}

      {wantsTechnicians && (
        <SetupSection title="Technicians">
          <p className="text-sm text-muted-foreground">
            Add your plumber, electrician and other contacts from Settings → Technicians once your property is in.
            Until then, Mira flags maintenance issues to you directly.
          </p>
        </SetupSection>
      )}

      <StepButtons onBack={onBack} submitLabel="Continue" submitting={submitting} />
    </form>
  );
}

function SetupSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-lg border bg-card p-4">
      <h2 className="text-sm font-medium">{title}</h2>
      {children}
    </section>
  );
}

function ImportStatusLine({ record }: { record: NonNullable<OnboardingState["first_property"]> }) {
  if (record.status === "completed") {
    return (
      <p className="flex items-center gap-2 text-sm">
        <Check className="size-4 text-[var(--status-live)]" />
        Imported {record.property_name ?? "your property"}.
      </p>
    );
  }
  if (record.status === "failed") {
    return (
      <p className="flex items-start gap-2 text-sm text-destructive">
        <CircleAlert className="mt-0.5 size-4 shrink-0" />
        {record.error ?? "Import failed."} Fix the link and continue to try again.
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-[var(--status-progress)]" />
      Importing from Airbnb — this keeps going even if you close this page.
    </p>
  );
}

// ── Step 4: review and continue ──────────────────────────────────────
function ReviewStep({
  capabilities,
  onboarding,
  onBack,
  onRefreshCapabilities,
  onGoToProfile,
}: {
  capabilities: HostCapabilities;
  onboarding: OnboardingState;
  onBack: () => void;
  onRefreshCapabilities: () => void;
  onGoToProfile: () => void;
}) {
  const router = useRouter();
  const { setState, refetch } = useOnboarding();
  const [submitting, setSubmitting] = useState(false);
  const importing = onboarding.first_property?.status === "importing";

  // While the import runs, keep the review current (it changes property-
  // dependent readiness); the server is doing the actual work.
  useEffect(() => {
    if (!importing) return;
    const timer = window.setInterval(() => void refetch(), 5000);
    return () => window.clearInterval(timer);
  }, [importing, refetch]);

  useEffect(() => {
    if (onboarding.first_property?.status === "completed") onRefreshCapabilities();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-check readiness once, when the import lands
  }, [onboarding.first_property?.status]);

  const selected = new Set(onboarding.selected_capabilities);
  const shown = capabilities.capabilities.filter((c) => selected.has(c.id) || !c.selectable);
  const chosen = shown.filter((c) => c.selectable);
  const included = shown.filter((c) => !c.selectable);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      setState(await api.onboarding.complete());
      router.push("/dashboard");
    } catch (err) {
      toast.error(errorMessage(err, "Could not finish setup"));
      if (err instanceof ApiError && err.status === 400) onGoToProfile();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 pt-2">
      {onboarding.first_property && <ImportStatusLine record={onboarding.first_property} />}
      <ReviewList title="Your choices" items={chosen} empty="You didn't pick any optional features." />
      <ReviewList title="Included with Mira" items={included} />
      <p className="text-xs text-muted-foreground">
        Anything marked &ldquo;Needs setup&rdquo; keeps working where it can — finish it any time from Settings →
        Features &amp; modules.
      </p>
      <StepButtons onBack={onBack} submitLabel="Go to dashboard" submitting={submitting} />
    </form>
  );
}

function ReviewList({ title, items, empty }: { title: string; items: CapabilityStatus[]; empty?: string }) {
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium">{title}</h2>
      {items.length === 0 && empty && <p className="text-sm text-muted-foreground">{empty}</p>}
      {items.length > 0 && (
      <ul className="divide-y rounded-lg border bg-card">
        {items.map((cap) => (
          <li key={cap.id} className="space-y-1.5 px-3 py-2.5">
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 text-sm">{cap.name}</p>
              <CapabilityStateChip state={cap.state} />
            </div>
            <CapabilityActions requirements={cap.requirements.filter((r) => r.hard)} linkActions={false} />
          </li>
        ))}
      </ul>
      )}
    </section>
>>>>>>> 368377a (20th October)
  );
}
