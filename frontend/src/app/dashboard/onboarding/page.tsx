"use client";

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

const HOST_STATUS_OPTIONS: { value: AirbnbHostStatus; label: string }[] = [
  { value: "new_host", label: "New host" },
  { value: "individual_host", label: "Individual host" },
  { value: "superhost", label: "Superhost" },
  { value: "guest_favorite", label: "Guest Favorite" },
  { value: "professional_host", label: "Professional host" },
  { value: "prefer_not_to_say", label: "Prefer not to say" },
];

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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
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
    } finally {
      setSubmitting(false);
    }
  }

  return (
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
  );
}
