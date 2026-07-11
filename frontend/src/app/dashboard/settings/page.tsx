"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE_URL, ApiError, api, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SettingsPage() {
  const { user, refreshUser } = useAuth();
  const [leadExophone, setLeadExophone] = useState(user?.lead_exophone ?? "");
  const [submitting, setSubmitting] = useState(false);

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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Settings</h1>
        <p className="text-sm text-muted-foreground">Account details</p>
      </div>

      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>Host account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Row label="Name" value={user?.name ?? "—"} />
          <Row label="Email" value={user?.email ?? "—"} />
          <Row label="Phone" value={user?.phone ?? "—"} />
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

      <Card className="max-w-md">
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

      <Card className="max-w-md">
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

      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>Voice agent personalization</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-muted-foreground">
            Leave any field blank to use MIRA&apos;s default. The golden rules (never hallucinate
            pricing, always escalate when unsure, etc.) stay fixed regardless — these only change tone
            and wording.
          </p>
          <form onSubmit={handleSavePersonalization} className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="agent_first_message">
                First message
              </label>
              <Textarea
                id="agent_first_message"
                placeholder="Namaste {guest_name}! I'm Mira, calling on behalf of {host_name} about {property_name}."
                value={firstMessage}
                onChange={(e) => setFirstMessage(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Placeholders: {"{host_name}"}, {"{property_name}"}, {"{city}"}, {"{guest_name}"} — any
                that don&apos;t apply to a given call (e.g. {"{property_name}"} on the Lead Agent line)
                are left blank automatically.
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="agent_persona">
                Personality note
              </label>
              <Textarea
                id="agent_persona"
                placeholder="e.g. Sound like a warm, chatty local host -- informal, never corporate."
                value={persona}
                onChange={(e) => setPersona(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="agent_escalation_phrase">
                Escalation phrase
              </label>
              <Textarea
                id="agent_escalation_phrase"
                placeholder="e.g. One moment, let me get my colleague on the line for you."
                value={escalationPhrase}
                onChange={(e) => setEscalationPhrase(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">Said right before MIRA hands off to you.</p>
            </div>
            <Button type="submit" disabled={savingPersonalization}>
              Save personalization
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}
