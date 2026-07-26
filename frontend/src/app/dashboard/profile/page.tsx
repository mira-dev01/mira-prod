"use client";

import { useRef, useState } from "react";
import { Camera, Link2, MessageCircle, Pencil, Phone, Send } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Step-by-step profile completion -- deliberately separate from the more
// exhaustive Settings page. A host doesn't have to fill everything in at
// once; the dot strip below the name just tracks how much of it they have,
// field by field, in no particular order.
export default function ProfilePage() {
  const { user, loading, refreshUser } = useAuth();

  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [uploadingBanner, setUploadingBanner] = useState(false);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const bannerInputRef = useRef<HTMLInputElement>(null);

  const [nameOpen, setNameOpen] = useState(false);
  const [name, setName] = useState(user?.name ?? "");
  const [businessName, setBusinessName] = useState(user?.business_name ?? "");
  const [savingBasics, setSavingBasics] = useState(false);

  const [guestCallOpen, setGuestCallOpen] = useState(false);
  const [guestCallNumber, setGuestCallNumber] = useState(user?.lead_exophone ?? "");
  const [savingGuestCallNumber, setSavingGuestCallNumber] = useState(false);

  const [emailOpen, setEmailOpen] = useState(false);
  const [notificationEmail, setNotificationEmail] = useState(user?.notification_email ?? "");
  const [savingNotificationEmail, setSavingNotificationEmail] = useState(false);

  const [savingWhatsapp, setSavingWhatsapp] = useState(false);

  if (loading || !user) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }

  const steps = [
    !!user.photo_url,
    !!user.name,
    !!user.business_name,
    !!user.lead_exophone,
    !!user.notification_email,
    user.whatsapp_assist_enabled,
  ];
  const doneCount = steps.filter(Boolean).length;

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingPhoto(true);
    try {
      await api.auth.uploadPhoto(file);
      await refreshUser();
      toast.success("Photo updated");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload photo");
    } finally {
      setUploadingPhoto(false);
    }
  }

  async function handleBannerChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingBanner(true);
    try {
      await api.auth.uploadBanner(file);
      await refreshUser();
      toast.success("Background updated");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload background");
    } finally {
      setUploadingBanner(false);
    }
  }

  async function handleSaveBasics(e: React.FormEvent) {
    e.preventDefault();
    setSavingBasics(true);
    try {
      await api.auth.updateMe({ name: name || null, business_name: businessName || null });
      await refreshUser();
      toast.success("Saved");
      setNameOpen(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSavingBasics(false);
    }
  }

  async function handleSaveGuestCallNumber(e: React.FormEvent) {
    e.preventDefault();
    setSavingGuestCallNumber(true);
    try {
      await api.auth.updateMe({ lead_exophone: guestCallNumber || null });
      await refreshUser();
      toast.success("Saved");
      setGuestCallOpen(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSavingGuestCallNumber(false);
    }
  }

  async function handleSaveNotificationEmail(e: React.FormEvent) {
    e.preventDefault();
    setSavingNotificationEmail(true);
    try {
      await api.auth.updateMe({ notification_email: notificationEmail || null });
      await refreshUser();
      toast.success("Saved");
      setEmailOpen(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSavingNotificationEmail(false);
    }
  }

  async function handleToggleWhatsapp(checked: boolean) {
    setSavingWhatsapp(true);
    try {
      await api.auth.updateMe({ whatsapp_assist_enabled: checked });
      await refreshUser();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSavingWhatsapp(false);
    }
  }

  const displayName = user.business_name || user.name || user.email;

  return (
    <div className="-m-4 md:-m-6 -mt-[calc(3.5rem+1rem)] md:mt-0">
      <div className="overflow-hidden rounded-none border-b bg-card md:mx-6 md:mt-6 md:rounded-2xl md:border">
        {/* Banner */}
        <div
          className="relative h-40 w-full bg-muted bg-cover bg-center sm:h-52"
          style={user.banner_url ? { backgroundImage: `url(${user.banner_url})` } : undefined}
        >
          {!user.banner_url && (
            <div className="flex h-full w-full items-center justify-center">
              <p className="text-sm text-muted-foreground">Add a background image whenever you like</p>
            </div>
          )}
          <input
            ref={bannerInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
            className="hidden"
            onChange={handleBannerChange}
          />
          <button
            type="button"
            aria-label="Change background"
            disabled={uploadingBanner}
            onClick={() => bannerInputRef.current?.click()}
            className="absolute top-3 right-3 flex size-9 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm transition-colors hover:bg-background"
          >
            {uploadingBanner ? (
              <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : (
              <Link2 className="size-4" />
            )}
          </button>

          {/* Avatar straddles the banner/content boundary */}
          <div className="absolute -bottom-12 left-6 sm:left-8">
            <div className="relative">
              <Avatar className="size-24 sm:size-28 ring-4 ring-card">
                <AvatarImage src={user.photo_url ?? undefined} alt="" />
                <AvatarFallback className="text-2xl">
                  {(user.name ?? user.email).charAt(0).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <input
                ref={photoInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
                className="hidden"
                onChange={handlePhotoChange}
              />
              <button
                type="button"
                aria-label="Change photo"
                disabled={uploadingPhoto}
                onClick={() => photoInputRef.current?.click()}
                className="absolute right-0 bottom-0 flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm transition-colors hover:opacity-90"
              >
                {uploadingPhoto ? (
                  <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                ) : (
                  <Camera className="size-3.5" />
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Name + progress */}
        <div className="px-6 pt-16 pb-6 sm:px-8">
          <button
            type="button"
            onClick={() => setNameOpen(true)}
            className="group flex items-center gap-2 text-left"
          >
            <h1 className="font-heading text-3xl">{displayName}</h1>
            <Pencil className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
          <div className="mt-3 flex max-w-xs gap-1">
            {steps.map((done, i) => (
              <div key={i} className={`h-1.5 flex-1 rounded-full ${done ? "bg-primary" : "bg-muted"}`} />
            ))}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{doneCount} of {steps.length} steps complete</p>

          {/* Three boxes */}
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <button
              type="button"
              onClick={() => setGuestCallOpen(true)}
              className="flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-colors hover:bg-accent"
            >
              <span className="flex size-9 items-center justify-center rounded-full bg-accent-warm-bg text-[var(--accent-warm)]">
                <Phone className="size-4" />
              </span>
              <span className="text-sm font-medium">Guest Call Number</span>
              <span className="text-xs text-muted-foreground">{user.lead_exophone || "Not set"}</span>
            </button>

            <button
              type="button"
              onClick={() => setEmailOpen(true)}
              className="flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-colors hover:bg-accent"
            >
              <span className="flex size-9 items-center justify-center rounded-full bg-accent-warm-bg text-[var(--accent-warm)]">
                <Send className="size-4" />
              </span>
              <span className="text-sm font-medium">Email Escalation Summaries</span>
              <span className="truncate text-xs text-muted-foreground">
                {user.notification_email || `Using ${user.email}`}
              </span>
            </button>

            <div className="flex flex-col items-start gap-2 rounded-xl border p-4">
              <div className="flex w-full items-center justify-between">
                <span className="flex size-9 items-center justify-center rounded-full bg-accent-warm-bg text-[var(--accent-warm)]">
                  <MessageCircle className="size-4" />
                </span>
                <Switch
                  checked={user.whatsapp_assist_enabled}
                  disabled={savingWhatsapp}
                  onCheckedChange={handleToggleWhatsapp}
                />
              </div>
              <span className="text-sm font-medium">WhatsApp Assist</span>
              <span className="text-xs text-muted-foreground">
                {user.whatsapp_assist_enabled ? "Enabled" : "Early access"}
              </span>
            </div>
          </div>
        </div>
      </div>

      <RightPanel
        open={nameOpen}
        onOpenChange={setNameOpen}
        title="Name"
        footer={
          <RightPanelFooterButton type="submit" form="profile-basics-form" disabled={savingBasics}>
            {savingBasics ? "Saving…" : "Save"}
          </RightPanelFooterButton>
        }
      >
        <form id="profile-basics-form" onSubmit={handleSaveBasics} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="profile-name">Your name</Label>
            <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="profile-business-name">Business / portfolio name</Label>
            <Input
              id="profile-business-name"
              value={businessName}
              onChange={(e) => setBusinessName(e.target.value)}
              placeholder="e.g. Pause Projects Goa"
            />
          </div>
        </form>
      </RightPanel>

      <RightPanel
        open={guestCallOpen}
        onOpenChange={setGuestCallOpen}
        title="Guest call number"
        footer={
          <RightPanelFooterButton type="submit" form="profile-guest-call-form" disabled={savingGuestCallNumber}>
            {savingGuestCallNumber ? "Saving…" : "Save"}
          </RightPanelFooterButton>
        }
      >
        <form id="profile-guest-call-form" onSubmit={handleSaveGuestCallNumber} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="profile-guest-call-number">Number</Label>
            <Input
              id="profile-guest-call-number"
              value={guestCallNumber}
              onChange={(e) => setGuestCallNumber(e.target.value)}
              placeholder="+91XXXXXXXXXX"
            />
            <p className="text-xs text-muted-foreground">The number Mira answers guest calls on.</p>
          </div>
        </form>
      </RightPanel>

      <RightPanel
        open={emailOpen}
        onOpenChange={setEmailOpen}
        title="Email escalation summaries"
        footer={
          <RightPanelFooterButton type="submit" form="profile-email-form" disabled={savingNotificationEmail}>
            {savingNotificationEmail ? "Saving…" : "Save"}
          </RightPanelFooterButton>
        }
      >
        <form id="profile-email-form" onSubmit={handleSaveNotificationEmail} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="profile-notification-email">Email</Label>
            <Input
              id="profile-notification-email"
              type="email"
              value={notificationEmail}
              onChange={(e) => setNotificationEmail(e.target.value)}
              placeholder={user.email}
            />
            <p className="text-xs text-muted-foreground">Leave blank to use your login email.</p>
          </div>
        </form>
      </RightPanel>
    </div>
  );
}
