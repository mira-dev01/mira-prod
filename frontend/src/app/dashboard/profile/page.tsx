"use client";

import { useRef, useState } from "react";
import { Camera, ImagePlus, Pencil } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Cell, CellTitle, FieldRow, Hint, Sheet, WideCell } from "@/components/ui/sheet";
import { UseCasePicker } from "@/components/use-case-picker";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { UseCaseKey } from "@/lib/types";

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

  const [interestsOpen, setInterestsOpen] = useState(false);
  const [interests, setInterests] = useState<UseCaseKey[]>(user?.interests ?? []);
  const [savingInterests, setSavingInterests] = useState(false);

  if (loading || !user) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }

  const steps = [
    !!user.photo_url,
    !!user.name,
    !!user.business_name,
    !!user.lead_exophone,
    !!user.notification_email,
    user.interests.length > 0,
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

  function toggleInterest(key: UseCaseKey) {
    setInterests((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  async function handleSaveInterests(e: React.FormEvent) {
    e.preventDefault();
    setSavingInterests(true);
    try {
      await api.auth.updateMe({ interests });
      await refreshUser();
      toast.success("Saved");
      setInterestsOpen(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSavingInterests(false);
    }
  }

  const displayName = user.business_name || user.name || user.email;
  const settingCell =
    "col-span-12 flex min-w-0 flex-col gap-2 border-r border-b border-foreground bg-card/70 p-5 text-left transition-colors hover:bg-accent md:col-span-4";

  return (
    <div className="space-y-6">
      <Sheet>
        <Cell className="gap-0 p-0">
          {/* Banner */}
          <div
            className="relative h-36 w-full border-b border-border bg-muted bg-cover bg-center sm:h-44"
            style={user.banner_url ? { backgroundImage: `url(${user.banner_url})` } : undefined}
          >
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
              className="absolute top-3 right-3 flex size-9 items-center justify-center border border-foreground bg-card text-foreground transition-colors hover:bg-accent"
            >
              {uploadingBanner ? (
                <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <ImagePlus className="size-4" />
              )}
            </button>

            {/* Avatar straddles the banner/content boundary */}
            <div className="absolute -bottom-12 left-5 sm:left-6">
              <div className="relative">
                <Avatar className="size-24 ring-4 ring-card sm:size-28">
                  <AvatarImage src={user.photo_url ?? undefined} alt="" />
                  <AvatarFallback className="text-2xl">{(user.name ?? user.email).charAt(0).toUpperCase()}</AvatarFallback>
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
                  className="absolute right-0 bottom-0 flex size-8 items-center justify-center rounded-full bg-foreground text-background transition-colors hover:opacity-90"
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
          <div className="space-y-3 px-5 pt-16 pb-5 sm:px-6">
            <button type="button" onClick={() => setNameOpen(true)} className="group flex items-center gap-2 text-left">
              <h1 className="page-title">{displayName}</h1>
              <Pencil className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
            <div className="flex max-w-xs gap-1" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={doneCount}>
              {steps.map((done, i) => (
                <div key={i} className={`h-1 flex-1 ${done ? "bg-foreground" : "bg-border"}`} />
              ))}
            </div>
            <Hint>
              {doneCount} of {steps.length} steps complete
            </Hint>
          </div>
        </Cell>

        <button type="button" onClick={() => setGuestCallOpen(true)} className={settingCell}>
          <CellTitle>Guest call number</CellTitle>
          <span className="font-heading text-xl font-semibold tabular-nums">{user.lead_exophone || "Not set"}</span>
        </button>

        <button type="button" onClick={() => setEmailOpen(true)} className={settingCell}>
          <CellTitle>Escalation emails</CellTitle>
          <span className="truncate font-heading text-xl font-semibold">{user.notification_email || user.email}</span>
        </button>

        <button
          type="button"
          onClick={() => {
            setInterests(user.interests);
            setInterestsOpen(true);
          }}
          className={settingCell}
        >
          <CellTitle>What matters to you</CellTitle>
          <span className="font-heading text-xl font-semibold">
            {user.interests.length > 0 ? `${user.interests.length} selected` : "Not set"}
          </span>
        </button>
      </Sheet>

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
        <form id="profile-basics-form" onSubmit={handleSaveBasics}>
          <Sheet>
            <WideCell>
              <FieldRow label="Your name" htmlFor="profile-name">
                <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} />
              </FieldRow>
              <FieldRow label="Business name" htmlFor="profile-business-name">
                <Input
                  id="profile-business-name"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  placeholder="e.g. Pause Projects Goa"
                />
              </FieldRow>
            </WideCell>
          </Sheet>
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
        <form id="profile-guest-call-form" onSubmit={handleSaveGuestCallNumber}>
          <Sheet>
            <WideCell>
              <FieldRow label="Number" htmlFor="profile-guest-call-number">
                <Input
                  id="profile-guest-call-number"
                  value={guestCallNumber}
                  onChange={(e) => setGuestCallNumber(e.target.value)}
                  placeholder="+91XXXXXXXXXX"
                />
              </FieldRow>
            </WideCell>
          </Sheet>
        </form>
      </RightPanel>

      <RightPanel
        open={emailOpen}
        onOpenChange={setEmailOpen}
        title="Escalation emails"
        footer={
          <RightPanelFooterButton type="submit" form="profile-email-form" disabled={savingNotificationEmail}>
            {savingNotificationEmail ? "Saving…" : "Save"}
          </RightPanelFooterButton>
        }
      >
        <form id="profile-email-form" onSubmit={handleSaveNotificationEmail}>
          <Sheet>
            <WideCell>
              <FieldRow label="Email" htmlFor="profile-notification-email" hint="Blank uses your login email.">
                <Input
                  id="profile-notification-email"
                  type="email"
                  value={notificationEmail}
                  onChange={(e) => setNotificationEmail(e.target.value)}
                  placeholder={user.email}
                />
              </FieldRow>
            </WideCell>
          </Sheet>
        </form>
      </RightPanel>

      <RightPanel
        open={interestsOpen}
        onOpenChange={setInterestsOpen}
        title="What matters to you"
        size="lg"
        footer={
          <RightPanelFooterButton type="submit" form="profile-interests-form" disabled={savingInterests}>
            {savingInterests ? "Saving…" : "Save"}
          </RightPanelFooterButton>
        }
      >
        <form id="profile-interests-form" onSubmit={handleSaveInterests}>
          <Sheet>
            <WideCell>
              <div className="col-span-12 p-5">
                <Hint className="mb-3">Pick any. This only changes what we show first.</Hint>
                <UseCasePicker selected={interests} onToggle={toggleInterest} />
              </div>
            </WideCell>
          </Sheet>
        </form>
      </RightPanel>
    </div>
  );
}
