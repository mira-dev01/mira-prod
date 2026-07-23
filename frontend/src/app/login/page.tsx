"use client";

import { Suspense, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth-context";
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

const HOST_REG_STEPS = ["Your details", "Hosting profile", "Voice agent intro", "First property"] as const;

function LoginPageInner() {
  const { login, registerHost } = useAuth();
  const searchParams = useSearchParams();
  const initialTab = searchParams.get("tab") === "register" ? "register" : "login";
  const [submitting, setSubmitting] = useState(false);

  // Pre-filled with the public demo account so anyone opening this URL for a
  // demo can just click "Log in" without being handed credentials separately.
  const [loginEmail, setLoginEmail] = useState("demo@mira.ai");
  const [loginPassword, setLoginPassword] = useState("MiraDemo2024");

  const [regStep, setRegStep] = useState(0);

  // Step 1 -- identity
  const [regName, setRegName] = useState("");
  const [regBusinessName, setRegBusinessName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPhone, setRegPhone] = useState("");
  const [regPassword, setRegPassword] = useState("");

  // Step 2 -- hosting profile
  const [regBusinessPhone, setRegBusinessPhone] = useState("");
  const [regHostStatus, setRegHostStatus] = useState<AirbnbHostStatus | "">("");
  const [regPropertyCount, setRegPropertyCount] = useState("");

  // Step 3 -- voice agent intro
  const [regFirstMessage, setRegFirstMessage] = useState("");
  const [isRecordingIntro, setIsRecordingIntro] = useState(false);
  const [introRecordedUrl, setIntroRecordedUrl] = useState<string | null>(null);
  const [transcribingIntro, setTranscribingIntro] = useState(false);
  const introMediaRecorderRef = useRef<MediaRecorder | null>(null);
  const introChunksRef = useRef<Blob[]>([]);

  // Step 4 -- first property
  const [regAirbnbUrl, setRegAirbnbUrl] = useState("");
  const [regIcalUrl, setRegIcalUrl] = useState("");

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(loginEmail, loginPassword);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  function goToNextStep(e: React.FormEvent) {
    e.preventDefault();
    setRegStep((s) => Math.min(s + 1, HOST_REG_STEPS.length - 1));
  }

  function goToPreviousStep() {
    setRegStep((s) => Math.max(s - 1, 0));
  }

  async function startIntroRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      introChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) introChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(introChunksRef.current, { type: "audio/webm" });
        setIntroRecordedUrl(URL.createObjectURL(blob));
        stream.getTracks().forEach((track) => track.stop());
        setTranscribingIntro(true);
        try {
          const { text } = await api.auth.transcribeRegistrationIntro(blob);
          setRegFirstMessage(text);
        } catch (err) {
          toast.error(err instanceof ApiError ? err.message : "Could not transcribe recording -- type it instead");
        } finally {
          setTranscribingIntro(false);
        }
      };
      recorder.start();
      introMediaRecorderRef.current = recorder;
      setIsRecordingIntro(true);
    } catch {
      toast.error("Could not access the microphone -- check browser permissions");
    }
  }

  function stopIntroRecording() {
    introMediaRecorderRef.current?.stop();
    setIsRecordingIntro(false);
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await registerHost({
        email: regEmail,
        password: regPassword,
        name: regName,
        phone: regPhone || undefined,
        business_name: regBusinessName || undefined,
        business_phone: regBusinessPhone,
        airbnb_host_status: regHostStatus || undefined,
        property_count_estimate: regPropertyCount ? Number(regPropertyCount) : undefined,
        agent_first_message: regFirstMessage || undefined,
        airbnb_url: regAirbnbUrl,
        ical_url: regIcalUrl || undefined,
      });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="brand-logo text-3xl">mira</CardTitle>
          <CardDescription>Sign in to manage your properties and live calls</CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue={initialTab} onValueChange={() => setRegStep(0)}>
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="login">Log in</TabsTrigger>
              <TabsTrigger value="register">Register</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <form onSubmit={handleLogin} className="space-y-4 pt-4">
                <div className="space-y-2">
                  <Label htmlFor="login-email">Email</Label>
                  <Input
                    id="login-email"
                    type="email"
                    required
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                    onFocus={(e) => e.target.select()}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="login-password">Password</Label>
                  <Input
                    id="login-password"
                    type="password"
                    required
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    onFocus={(e) => e.target.select()}
                  />
                </div>
                <Button type="submit" className="w-full" disabled={submitting}>
                  Log in
                </Button>
                <p className="text-center text-xs text-muted-foreground">
                  Demo credentials pre-filled — just click Log in.
                </p>
              </form>
            </TabsContent>
            <TabsContent value="register">
              <div className="flex items-center justify-center gap-2 pt-4 pb-2">
                {HOST_REG_STEPS.map((label, i) => (
                  <div key={label} className="flex items-center gap-2">
                    <div
                      className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                        i === regStep
                          ? "bg-primary text-primary-foreground"
                          : i < regStep
                            ? "bg-primary/20 text-primary"
                            : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {i + 1}
                    </div>
                    {i < HOST_REG_STEPS.length - 1 && <div className="h-px w-4 bg-border" />}
                  </div>
                ))}
              </div>
              <p className="pb-2 text-center text-xs text-muted-foreground">{HOST_REG_STEPS[regStep]}</p>

              {regStep === 0 && (
                <form onSubmit={goToNextStep} className="space-y-4 pt-2">
                  <div className="space-y-2">
                    <Label htmlFor="reg-name">Your name</Label>
                    <Input id="reg-name" required value={regName} onChange={(e) => setRegName(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-business-name">Business name (optional)</Label>
                    <Input
                      id="reg-business-name"
                      value={regBusinessName}
                      onChange={(e) => setRegBusinessName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-email">Email</Label>
                    <Input
                      id="reg-email"
                      type="email"
                      required
                      value={regEmail}
                      onChange={(e) => setRegEmail(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-phone">Personal phone (optional)</Label>
                    <Input id="reg-phone" value={regPhone} onChange={(e) => setRegPhone(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-password">Password</Label>
                    <Input
                      id="reg-password"
                      type="password"
                      required
                      minLength={8}
                      value={regPassword}
                      onChange={(e) => setRegPassword(e.target.value)}
                    />
                  </div>
                  <Button type="submit" className="w-full">
                    Continue
                  </Button>
                </form>
              )}

              {regStep === 1 && (
                <form onSubmit={goToNextStep} className="space-y-4 pt-2">
                  <div className="space-y-2">
                    <Label htmlFor="reg-business-phone">Business number</Label>
                    <Input
                      id="reg-business-phone"
                      required
                      placeholder="+91XXXXXXXXXX"
                      value={regBusinessPhone}
                      onChange={(e) => setRegBusinessPhone(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      The number your voice agent will run on for guest calls.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-host-status">Airbnb host status (optional)</Label>
                    <Select
                      value={regHostStatus}
                      onValueChange={(v) => setRegHostStatus((v as AirbnbHostStatus) || "")}
                    >
                      <SelectTrigger id="reg-host-status">
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
                    <Label htmlFor="reg-property-count">Approx. number of properties (optional)</Label>
                    <Input
                      id="reg-property-count"
                      type="number"
                      min={1}
                      value={regPropertyCount}
                      onChange={(e) => setRegPropertyCount(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goToPreviousStep}>
                      Back
                    </Button>
                    <Button type="submit" className="min-w-0 flex-1">
                      Continue
                    </Button>
                  </div>
                </form>
              )}

              {regStep === 2 && (
                <form onSubmit={goToNextStep} className="space-y-4 pt-2">
                  <div className="space-y-2">
                    <Label htmlFor="reg-first-message">Add your voice agent&apos;s intro</Label>
                    <Textarea
                      id="reg-first-message"
                      placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
                      value={regFirstMessage}
                      onChange={(e) => setRegFirstMessage(e.target.value)}
                      disabled={transcribingIntro}
                    />
                    <p className="text-xs text-muted-foreground">
                      Optional — leave blank to use MIRA&apos;s default greeting. Placeholders:{" "}
                      {"{host_name}"}, {"{property_name}"}, {"{city}"}, {"{guest_name}"} — any that
                      don&apos;t apply to a given call are left blank automatically.
                    </p>
                  </div>

                  <div className="space-y-2 rounded-md border p-3">
                    <Label>Or record it instead</Label>
                    <div className="flex items-center gap-3">
                      {!isRecordingIntro ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="shrink-0"
                          disabled={transcribingIntro}
                          onClick={startIntroRecording}
                        >
                          {introRecordedUrl ? "Re-record" : "Start recording"}
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          className="shrink-0"
                          onClick={stopIntroRecording}
                        >
                          Stop recording
                        </Button>
                      )}
                      {introRecordedUrl && <audio controls src={introRecordedUrl} className="h-8 min-w-0 flex-1" />}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {transcribingIntro
                        ? "Transcribing your recording…"
                        : introRecordedUrl
                          ? "Transcribed below — edit the text above if needed."
                          : "Speak your greeting and it'll be transcribed into the field above."}
                    </p>
                  </div>

                  <div className="flex gap-2">
                    <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goToPreviousStep}>
                      Back
                    </Button>
                    <Button type="submit" className="min-w-0 flex-1" disabled={transcribingIntro}>
                      Continue
                    </Button>
                  </div>
                </form>
              )}

              {regStep === 3 && (
                <form onSubmit={handleRegister} className="space-y-4 pt-2">
                  <div className="space-y-2">
                    <Label htmlFor="reg-airbnb-url">Airbnb listing URL</Label>
                    <Input
                      id="reg-airbnb-url"
                      required
                      placeholder="https://www.airbnb.co.in/rooms/12345678"
                      value={regAirbnbUrl}
                      onChange={(e) => setRegAirbnbUrl(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      We&apos;ll import this property automatically. Add the rest later from the Properties page.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-ical-url">iCal link (optional)</Label>
                    <Input
                      id="reg-ical-url"
                      value={regIcalUrl}
                      onChange={(e) => setRegIcalUrl(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" className="min-w-0 flex-1" onClick={goToPreviousStep}>
                      Back
                    </Button>
                    <Button type="submit" className="min-w-0 flex-1" disabled={submitting}>
                      Create account
                    </Button>
                  </div>
                </form>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  // useSearchParams requires a Suspense boundary -- see Next.js docs (this
  // route is otherwise fully client-rendered, so the fallback is instant).
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}
