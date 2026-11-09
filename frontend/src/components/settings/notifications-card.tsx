"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Segmented } from "@/components/settings/segmented";
import { Hint, SheetFooter, SheetHeading, SheetRow, WideCell } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { LEAD_BUCKETS, DEFAULT_LEAD_LABELS } from "@/lib/leads";
import type { NotificationPreferences } from "@/lib/types";

type Prefs = NotificationPreferences;

/**
 * Settings > Your account > Notifications & escalations: how the host is
 * told about -- and pulled into -- guest calls. Everything here changes
 * real behaviour (backend app/services/notification_preferences_service.py
 * and the senders that read it). What can't be switched off: the in-app
 * notification and the lead for every escalation, transfer and busy call.
 */
export function NotificationsCard() {
  const { user, setUserData } = useAuth();
  const { data, loading, error, refetch, setData } = useAsync(() => api.notificationSettings.get(), []);
  const [draft, setDraft] = useState<Prefs | null>(null);
  const [email, setEmail] = useState(user?.notification_email ?? "");
  const [saving, setSaving] = useState(false);

  if (loading && !data) {
    return (
      <WideCell>
        <div className="col-span-12 p-5">
          <Skeleton className="h-40 w-full" />
        </div>
      </WideCell>
    );
  }
  if (error || !data) {
    return (
      <WideCell>
        <div className="col-span-12 space-y-2 p-5 text-sm">
          <p className="text-muted-foreground">Couldn&apos;t load your notification settings.</p>
          <Button variant="outline" size="sm" onClick={refetch}>
            Try again
          </Button>
        </div>
      </WideCell>
    );
  }

  const prefs = draft ?? data.preferences;
  const emailChanged = (email.trim() || null) !== (user?.notification_email ?? null);
  const dirty = draft !== null || emailChanged;

  function set<K extends keyof Prefs>(key: K, value: Prefs[K]) {
    setDraft({ ...prefs, [key]: value });
  }

  async function handleSave() {
    if (!user) return;
    setSaving(true);
    try {
      let nextUser = user;
      if (emailChanged) nextUser = await api.auth.updateMe({ notification_email: email.trim() || null });
      if (draft !== null) {
        const saved = await api.notificationSettings.update(draft);
        setData(saved);
        nextUser = { ...nextUser, notification_preferences: saved.preferences };
      }
      setUserData(nextUser);
      setDraft(null);
      toast.success("Notification settings saved");
    } catch (err) {
      // Keep the draft so nothing typed is lost.
      toast.error(err instanceof ApiError ? err.message : "Couldn't save your notification settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <WideCell id="notifications">
      <SheetHeading title="Notifications &amp; escalations">
        Every escalation, transfer and busy call always shows up in your dashboard and creates a lead. These
        settings choose what else happens.
      </SheetHeading>

      <SheetRow title="Email" hint="Where call summaries and escalations are sent.">
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-3">
              <CheckRow
                id="notify-call-summary"
                checked={prefs.call_summary_email}
                onChange={(v) => set("call_summary_email", v)}
                label="Call summary after every call"
              />
              <CheckRow
                id="notify-escalation-email"
                checked={prefs.escalation_email}
                onChange={(v) => set("escalation_email", v)}
                label="Escalation emails"
              />
            </div>
            {(prefs.call_summary_email || prefs.escalation_email) && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="notification-email">Send to</Label>
                <Input
                  id="notification-email"
                  type="email"
                  placeholder={user?.email ?? "you@example.com"}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <Hint>Leave blank to use your login email.</Hint>
              </div>
            )}
          </div>
          {prefs.call_summary_email && (
            <EmailTemplateEditor
              subject={prefs.call_summary_subject}
              body={prefs.call_summary_body}
              defaultSubject={data.default_subject}
              defaultBody={data.default_body}
              placeholders={data.placeholders}
              onChange={(subject, body) => setDraft({ ...prefs, call_summary_subject: subject, call_summary_body: body })}
            />
          )}
        </div>
      </SheetRow>

      <SheetRow title="Lead labels" hint="Your names for each lead tier. Used on the dashboard, Leads and in email subjects.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {LEAD_BUCKETS.map((bucket) => (
            <div key={bucket} className="flex flex-col gap-1.5">
              <Label htmlFor={`lead-label-${bucket}`} className="text-[13px] font-medium text-muted-foreground">
                {DEFAULT_LEAD_LABELS[bucket]}
              </Label>
              <Input
                id={`lead-label-${bucket}`}
                maxLength={24}
                value={prefs.lead_labels[bucket]}
                onChange={(e) => set("lead_labels", { ...prefs.lead_labels, [bucket]: e.target.value })}
              />
            </div>
          ))}
        </div>
      </SheetRow>

      <SheetRow title="Guest requests" hint="How Mira reaches you when a guest needs a person.">
        <div className="flex max-w-xl flex-col gap-6">
          <ChoiceRow
            label="In-stay problems and questions Mira can't answer"
            value={prefs.stay_request_handling}
            onChange={(v) => set("stay_request_handling", v)}
            options={[
              { value: "whatsapp", label: "WhatsApp me the details" },
              { value: "live_transfer", label: "Transfer the call to me" },
            ]}
          />
          <ChoiceRow
            label="A guest asks to speak with you"
            value={prefs.connect_request_handling}
            onChange={(v) => set("connect_request_handling", v)}
            options={[
              { value: "live_transfer", label: "Transfer the call to me" },
              { value: "whatsapp", label: "WhatsApp me their details" },
            ]}
          />
        </div>
      </SheetRow>

      <SheetRow title="WhatsApp alerts" hint="Sent to your host transfer number.">
        <div className="flex flex-col gap-3">
          <CheckRow
            id="notify-busy"
            checked={prefs.busy_call_alert}
            onChange={(v) => set("busy_call_alert", v)}
            label="Busy-call alert, so you can call the guest back"
          />
          <CheckRow
            id="notify-calling"
            checked={prefs.guest_calling_alert}
            onChange={(v) => set("guest_calling_alert", v)}
            label="A guest is calling, with a link to take the call"
          />
          <CheckRow
            id="notify-reply"
            checked={prefs.guest_reply_alert}
            onChange={(v) => set("guest_reply_alert", v)}
            label="A guest replied to Mira's WhatsApp follow-up"
          />
        </div>
      </SheetRow>

      <SheetRow title="Missed calls">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>Mira calls back guests you missed, later</span>
          <Badge variant="outline">Coming soon</Badge>
        </div>
        <Hint>Outbound calls will be charged to your wallet.</Hint>
      </SheetRow>

      <SheetFooter>
        <Button onClick={handleSave} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save notification settings"}
        </Button>
      </SheetFooter>
    </WideCell>
  );
}

function CheckRow({
  id,
  checked,
  onChange,
  label,
}: {
  id: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label htmlFor={id} className="flex items-center gap-3 text-sm">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      {label}
    </label>
  );
}

function ChoiceRow<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">{label}</p>
      <Segmented label={label} value={value} onChange={onChange} options={options} />
    </div>
  );
}

/** Subject + optional custom body, placeholders insertable at the cursor,
 * and a preview rendered by the backend's real email renderer. */
function EmailTemplateEditor({
  subject,
  body,
  defaultSubject,
  defaultBody,
  placeholders,
  onChange,
}: {
  subject: string | null;
  body: string | null;
  defaultSubject: string;
  defaultBody: string;
  placeholders: string[];
  onChange: (subject: string | null, body: string | null) => void;
}) {
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const lastFocused = useRef<"subject" | "body">("subject");
  const [preview, setPreview] = useState<{ subject: string; html: string } | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const subjectValue = subject ?? defaultSubject;

  function insert(token: string) {
    const target = lastFocused.current === "body" && body !== null ? bodyRef.current : subjectRef.current;
    const current = lastFocused.current === "body" && body !== null ? body : subjectValue;
    const start = target?.selectionStart ?? current.length;
    const end = target?.selectionEnd ?? current.length;
    const next = `${current.slice(0, start)}{${token}}${current.slice(end)}`;
    if (lastFocused.current === "body" && body !== null) onChange(subject, next);
    else onChange(next, body);
  }

  async function handlePreview() {
    setPreviewing(true);
    try {
      setPreview(await api.notificationSettings.preview({ subject: subjectValue, body }));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't build the preview");
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <div className="space-y-4 border-t border-border pt-4 lg:border-t-0 lg:pt-0">
      <div className="space-y-1.5">
        <Label htmlFor="summary-subject">Call summary subject</Label>
        <Input
          id="summary-subject"
          ref={subjectRef}
          maxLength={200}
          value={subjectValue}
          onFocus={() => (lastFocused.current = "subject")}
          onChange={(e) => onChange(e.target.value, body)}
        />
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="summary-body">Call summary body</Label>
          {body === null ? (
            <Button type="button" variant="outline" size="sm" onClick={() => onChange(subject, defaultBody)}>
              Customise
            </Button>
          ) : (
            <Button type="button" variant="outline" size="sm" onClick={() => onChange(subject, null)}>
              Use standard layout
            </Button>
          )}
        </div>
        {body === null ? (
          <p className="text-[13px] text-muted-foreground">Using the standard summary layout.</p>
        ) : (
          <Textarea
            id="summary-body"
            ref={bodyRef}
            rows={6}
            maxLength={4000}
            value={body}
            onFocus={() => (lastFocused.current = "body")}
            onChange={(e) => onChange(subject, e.target.value)}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] text-muted-foreground">Insert:</span>
        {placeholders.map((p) => (
          <button
            key={p}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => insert(p)}
            className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {`{${p}}`}
          </button>
        ))}
      </div>
      <div className="space-y-2">
        <Button type="button" variant="outline" size="sm" onClick={handlePreview} disabled={previewing}>
          {previewing ? "Building preview…" : "Preview email"}
        </Button>
        {preview && (
          <div className="space-y-1.5">
            <p className="text-xs">
              <span className="text-muted-foreground">Subject: </span>
              {preview.subject}
            </p>
            <iframe
              title="Call summary email preview"
              sandbox=""
              srcDoc={preview.html}
              className="h-96 w-full rounded-md border bg-white"
            />
          </div>
        )}
      </div>
    </div>
  );
}
