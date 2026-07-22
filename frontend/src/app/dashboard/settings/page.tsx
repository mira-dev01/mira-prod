"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CreditCard, KeyRound, Users } from "lucide-react";
import { DefinitionRow } from "@/components/ui/definition-row";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AiTrainingSection } from "@/components/settings/ai-training-section";
import { TechniciansSection } from "@/components/settings/technicians-section";
import { API_BASE_URL, ApiError, api, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const VALID_TABS = ["workspace", "voice-ai", "technicians", "ai-training", "billing", "api", "team"] as const;
type SettingsTab = (typeof VALID_TABS)[number];

function ComingSoonTab({ icon: Icon, label }: { icon: React.ComponentType<{ className?: string }>; label: string }) {
  return (
    <Card className="max-w-md">
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        <span className="flex size-10 items-center justify-center rounded-full bg-muted">
          <Icon className="size-5 text-muted-foreground" />
        </span>
        <div className="space-y-1">
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">Coming soon.</p>
        </div>
      </CardContent>
    </Card>
  );
}

function SettingsPageContent() {
  const { user, loading, refreshUser } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialTab = searchParams.get("tab");
  const [tab, setTab] = useState<SettingsTab>(
    VALID_TABS.includes(initialTab as SettingsTab) ? (initialTab as SettingsTab) : "workspace"
  );

  function handleTabChange(value: unknown) {
    if (typeof value !== "string" || !VALID_TABS.includes(value as SettingsTab)) return;
    setTab(value as SettingsTab);
    // Keeps the URL deep-linkable (e.g. the Pricing page's "Review
    // negotiation policy" link opens straight to ?tab=ai-training) without
    // adding a history entry for every tab click.
    router.replace(`/dashboard/settings?tab=${value}`, { scroll: false });
  }

  const [leadExophone, setLeadExophone] = useState(user?.lead_exophone ?? "");
  const [submitting, setSubmitting] = useState(false);

  const [phone, setPhone] = useState(user?.phone ?? "");
  const [savingPhone, setSavingPhone] = useState(false);

  const [notificationEmail, setNotificationEmail] = useState(user?.notification_email ?? "");
  const [savingNotificationEmail, setSavingNotificationEmail] = useState(false);

  const [firstMessage, setFirstMessage] = useState(user?.agent_first_message ?? "");
  const [persona, setPersona] = useState(user?.agent_persona ?? "");
  const [escalationPhrase, setEscalationPhrase] = useState(user?.agent_escalation_phrase ?? "");
  const [savingPersonalization, setSavingPersonalization] = useState(false);

  async function handleSaveLeadExophone(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.auth.updateMe({ lead_exophone: leadExophone || null });
      await refreshUser();
      toast.success("Lead intake number saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save lead intake number");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSavePhone(e: React.FormEvent) {
    e.preventDefault();
    setSavingPhone(true);
    try {
      await api.auth.updateMe({ phone: phone || null });
      await refreshUser();
      toast.success("Phone number saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save phone number");
    } finally {
      setSavingPhone(false);
    }
  }

  async function handleSaveNotificationEmail(e: React.FormEvent) {
    e.preventDefault();
    setSavingNotificationEmail(true);
    try {
      await api.auth.updateMe({ notification_email: notificationEmail || null });
      await refreshUser();
      toast.success("Notification email saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save notification email");
    } finally {
      setSavingNotificationEmail(false);
    }
  }

  async function handleSavePersonalization(e: React.FormEvent) {
    e.preventDefault();
    setSavingPersonalization(true);
    try {
      await api.auth.updateMe({
        agent_first_message: firstMessage || null,
        agent_persona: persona || null,
        agent_escalation_phrase: escalationPhrase || null,
      });
      await refreshUser();
      toast.success("Voice agent personalization saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save personalization");
    } finally {
      setSavingPersonalization(false);
    }
  }

  function handleTestLeadAgent() {
    const token = getToken();
    if (!token) {
      toast.error("Not logged in");
      return;
    }
    const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const url = `${backendOrigin}/api/v1/voice/test?token=${encodeURIComponent(token)}`;
    window.open(url, "_blank");
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="text-sm text-muted-foreground">Account details</p>
        </div>
        {[5, 3, 4, 6].map((rows, i) => (
          <Card key={i} className="max-w-md">
            <CardHeader>
              <Skeleton variant="text" className="w-1/2" />
            </CardHeader>
            <CardContent className="space-y-3">
              {Array.from({ length: rows }, (_, j) => (
                <Skeleton key={j} variant="text" />
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Settings</h1>
        <p className="text-sm text-muted-foreground">Account details</p>
      </div>

      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="workspace">Workspace</TabsTrigger>
          <TabsTrigger value="voice-ai">Voice AI</TabsTrigger>
          <TabsTrigger value="technicians">Technicians</TabsTrigger>
          <TabsTrigger value="ai-training">AI Training</TabsTrigger>
          <TabsTrigger value="billing">Billing</TabsTrigger>
          <TabsTrigger value="api">API</TabsTrigger>
          <TabsTrigger value="team">Team</TabsTrigger>
        </TabsList>

        <TabsContent value="workspace" className="pt-4">
          <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
            <Card>
              <CardHeader>
                <CardTitle>Host account</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <DefinitionRow label="Name" value={user?.name ?? "—"} />
                <DefinitionRow label="Email" value={user?.email ?? "—"} />
                <DefinitionRow label="Phone" value={user?.phone ?? "—"} />
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Plan tier</span>
                  <Badge variant="secondary" className="capitalize">
                    {user?.tier ?? "—"}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Status</span>
                  <Badge variant="outline" className="capitalize">
                    {user?.status ?? "—"}
                  </Badge>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>WhatsApp escalations</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="mb-3 text-sm text-muted-foreground">
                  Your phone number for escalation WhatsApp messages, in addition to email. After saving,
                  text &quot;join &lt;code&gt;&quot; to the Twilio Sandbox number from this phone — the
                  sandbox only delivers to numbers that have opted in.
                </p>
                <form onSubmit={handleSavePhone} className="flex gap-2">
                  <Input
                    placeholder="+9198XXXXXXXX"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                  />
                  <Button type="submit" disabled={savingPhone}>
                    Save
                  </Button>
                </form>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Escalation notifications</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="mb-3 text-sm text-muted-foreground">
                  Where escalation summaries are emailed. Leave blank to use your login email above — set
                  this if you&apos;d rather they go to a shared inbox (e.g. a front-desk address) instead.
                </p>
                <form onSubmit={handleSaveNotificationEmail} className="flex gap-2">
                  <Input
                    type="email"
                    placeholder={user?.email ?? "you@example.com"}
                    value={notificationEmail}
                    onChange={(e) => setNotificationEmail(e.target.value)}
                  />
                  <Button type="submit" disabled={savingNotificationEmail}>
                    Save
                  </Button>
                </form>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Lead intake number</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="mb-3 text-sm text-muted-foreground">
                  Calls to this number run the Lead Agent across your full property portfolio instead of one
                  property — for booking enquiries, not existing-guest support. This is also your general
                  testing link: it asks the caller which property they mean instead of testing just one.
                </p>
                <form onSubmit={handleSaveLeadExophone} className="flex gap-2">
                  <Input
                    placeholder="+9180XXXXXXXX"
                    value={leadExophone}
                    onChange={(e) => setLeadExophone(e.target.value)}
                  />
                  <Button type="submit" disabled={submitting}>
                    Save
                  </Button>
                </form>
                <Button variant="secondary" size="sm" className="mt-3" onClick={handleTestLeadAgent}>
                  Test Lead Agent in browser
                </Button>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="voice-ai" className="space-y-6 pt-4">
          <Card className="max-w-3xl">
            <CardHeader>
              <CardTitle>Voice agent personalization</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-sm text-muted-foreground">
                Leave any field blank to use MIRA&apos;s default. The golden rules (never hallucinate
                pricing, always escalate when unsure, etc.) stay fixed regardless — these only change
                tone and wording.
              </p>
              <form onSubmit={handleSavePersonalization} className="space-y-4">
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
        </TabsContent>

        <TabsContent value="technicians" className="pt-4">
          <TechniciansSection />
        </TabsContent>
        <TabsContent value="ai-training" className="pt-4">
          <AiTrainingSection />
        </TabsContent>

        <TabsContent value="billing" className="pt-4">
          <ComingSoonTab icon={CreditCard} label="Billing" />
        </TabsContent>
        <TabsContent value="api" className="pt-4">
          <ComingSoonTab icon={KeyRound} label="API access" />
        </TabsContent>
        <TabsContent value="team" className="pt-4">
          <ComingSoonTab icon={Users} label="Team members" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function SettingsPage() {
  // useSearchParams requires a Suspense boundary -- see Next.js docs (this
  // matches the pattern already used on the Leads/Pricing pages).
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <SettingsPageContent />
    </Suspense>
  );
}
