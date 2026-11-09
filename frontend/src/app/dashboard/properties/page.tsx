"use client";

import { Suspense, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { MoreHorizontal, Search, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ListRow } from "@/components/ui/list-row";
import { StatusChip } from "@/components/status-chip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Hint, Sheet, Toolbar, WideCell } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { PoliciesTab } from "@/components/policies-tab";
import { PropertyFaqManager } from "@/components/property-faq-manager";
import { PropertyFormSection } from "@/components/property-form-section";
import { PropertyFormFields } from "@/components/property-form-fields";
import { PolicyLabelChips, PropertyPoliciesInEffect } from "@/components/property-policies-in-effect";
import { PropertyRoomRentalTab } from "@/components/property-room-rental-tab";
import { PropertyPhotoCarousel } from "@/components/property-photo-carousel";
import { PropertyPhotosManager } from "@/components/property-photos-manager";
import { TalkToMiraDialog } from "@/components/talk-to-mira-dialog";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError, API_BASE_URL, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { matchesSearch } from "@/lib/utils";
import type { AirbnbUrlImportStatus, FaqEntryOut, PolicyOverview, PropertyCreate, PropertyOut } from "@/lib/types";

const emptyForm: PropertyCreate = {
  name: "",
  city: "",
  exophone: "",
  base_price: 0,
  ical_url: "",
  usp: "",
  house_rules: "",
  neighborhood_info: "",
  amenities: [],
  faq: [],
  seasonal_notes: [],
  check_in_time: "14:00",
  check_out_time: "11:00",
  max_guests: 4,
  minimum_nights: 1,
  saturday_minimum_stay_enabled: false,
  room_rental_enabled: false,
  exact_airbnb_pricing: false,
  is_premium: false,
};

function normalizeForSubmit(form: PropertyCreate): PropertyCreate {
  // Blank optional text fields must go as null, not "" -- exophone has a
  // unique constraint, so a second property saved with exophone: "" collides
  // with the first one's "" instead of being treated as "not set".
  return {
    ...form,
    city: form.city || null,
    exophone: form.exophone || null,
    ical_url: form.ical_url || null,
    usp: form.usp || null,
    house_rules: form.house_rules || null,
    neighborhood_info: form.neighborhood_info || null,
    room_details: form.room_details || null,
    room_ical_url: form.room_ical_url || null,
  };
}

function propertyToForm(property: PropertyOut): PropertyCreate {
  return {
    name: property.name,
    city: property.city ?? "",
    exophone: property.exophone ?? "",
    base_price: property.base_price,
    ical_url: property.ical_url ?? "",
    usp: property.usp ?? "",
    house_rules: property.house_rules ?? "",
    neighborhood_info: property.neighborhood_info ?? "",
    amenities: property.amenities,
    faq: property.faq as PropertyCreate["faq"],
    photos: property.photos,
    seasonal_notes: property.seasonal_notes,
    check_in_time: property.check_in_time,
    check_out_time: property.check_out_time,
    max_guests: property.max_guests,
    minimum_nights: property.minimum_nights,
    saturday_minimum_stay_enabled: property.saturday_minimum_stay_enabled,
    room_rental_enabled: property.room_rental_enabled,
    room_base_price: property.room_base_price,
    rentable_room_count: property.rentable_room_count,
    room_max_guests: property.room_max_guests,
    room_details: property.room_details ?? "",
    room_ical_url: property.room_ical_url ?? "",
    exact_airbnb_pricing: property.exact_airbnb_pricing,
    is_premium: property.is_premium,
  };
}

function PropertiesList({ policies, onOpenPolicies }: { policies: PolicyOverview | null; onOpenPolicies: () => void }) {
  const { isInternalOrg, user } = useAuth();
  const perPropertyTransfer = user?.notification_preferences.transfer_number_mode === "per_property";
  const { data: properties, loading, refetch } = useAsync(() => api.properties.list(), []);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<PropertyCreate>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [talkOpen, setTalkOpen] = useState(false);
  const [phoneBannerDismissed, setPhoneBannerDismissed] = useState(false);
  const [search, setSearch] = useState("");

  const filteredProperties = (properties ?? []).filter((property) =>
    matchesSearch(search, [
      property.name,
      property.city,
      property.exophone,
      property.usp,
      property.house_rules,
      property.neighborhood_info,
      ...property.amenities,
    ]),
  );

  const [airbnbUrlDialogOpen, setAirbnbUrlDialogOpen] = useState(false);
  const [airbnbUrlsText, setAirbnbUrlsText] = useState("");
  const [airbnbImportState, setAirbnbImportState] = useState<"idle" | "triggering" | "polling" | "done" | "failed">(
    "idle",
  );
  const [airbnbImportResult, setAirbnbImportResult] = useState<AirbnbUrlImportStatus | null>(null);

  // Derived, not new state -- airbnbUrlsText stays populated through
  // triggering/polling/done (only cleared by resetAirbnbImportDialog), so
  // the real submitted count is always recomputable from it. Used to show
  // "Importing N listings…" during polling instead of a vague message,
  // without fabricating any percentage/progress data the backend doesn't
  // actually provide (AirbnbUrlImportStatus.status is only
  // running|ready|failed).
  const airbnbUrlCount = useMemo(
    () =>
      airbnbUrlsText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean).length,
    [airbnbUrlsText],
  );

  const [editing, setEditing] = useState<PropertyOut | null>(null);
  const [editForm, setEditForm] = useState<PropertyCreate>(emptyForm);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editFaqEntries, setEditFaqEntries] = useState<FaqEntryOut[]>([]);
  const [loadingFaq, setLoadingFaq] = useState(false);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.properties.create(normalizeForSubmit(form));
      toast.success("Property added");
      setOpen(false);
      setForm(emptyForm);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create property");
    } finally {
      setSubmitting(false);
    }
  }

  function openEdit(property: PropertyOut) {
    setEditing(property);
    setEditForm(propertyToForm(property));
    setEditFaqEntries([]);
    setLoadingFaq(true);
    api.faq
      .list()
      .then((all) => setEditFaqEntries(all.filter((entry) => entry.property_id === property.id)))
      .catch((err) => toast.error(err instanceof ApiError ? err.message : "Failed to load FAQ entries"))
      .finally(() => setLoadingFaq(false));
  }

  async function handleSaveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSavingEdit(true);
    try {
      await api.properties.update(editing.id, normalizeForSubmit(editForm));
      toast.success("Property updated");
      setEditing(null);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update property");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.properties.remove(id);
      toast.success("Property removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove property");
    }
  }

  async function handleTestInBrowser(id: string) {
    const token = await getToken();
    if (!token) {
      toast.error("Not logged in");
      return;
    }
    const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const url = `${backendOrigin}/api/v1/voice/test?property_id=${id}&token=${encodeURIComponent(token)}`;
    window.open(url, "_blank");
  }

  function handleSetDiscount(id: string) {
    window.open(`/dashboard/pricing?property_id=${id}`, "_blank");
  }

  async function handleImportFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length === 0) return;

    setImporting(true);
    try {
      const results = await api.properties.importListings(files);
      const created = results.filter((r) => r.status === "created").length;
      const updated = results.filter((r) => r.status === "updated").length;
      const failed = results.filter((r) => r.status === "error");
      const totalFaqEntries = results.reduce((sum, r) => sum + r.faq_entries_created, 0);

      if (created || updated) {
        toast.success(
          `Imported ${files.length - failed.length} listing(s) — ${created} created, ${updated} updated, ` +
            `${totalFaqEntries} FAQ knowledge-base entries saved`,
        );
      }
      for (const failure of failed) {
        toast.error(`${failure.filename}: ${failure.error}`);
      }
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  }

  async function handleImportAirbnbUrls() {
    const urls = airbnbUrlsText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (urls.length === 0) return;

    setAirbnbImportState("triggering");
    setAirbnbImportResult(null);
    try {
      const { snapshot_id } = await api.properties.importAirbnbUrls(urls);
      setAirbnbImportState("polling");

      // Bright Data's scrape job runs async server-side (seconds to a
      // couple minutes depending on listing count) -- poll rather than
      // block on one long request. ~4s interval, capped at 5 minutes so a
      // stuck job doesn't poll forever.
      const maxAttempts = 75;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
        const result = await api.properties.importAirbnbUrlsStatus(snapshot_id);
        if (result.status === "running") continue;

        setAirbnbImportResult(result);
        setAirbnbImportState(result.status === "ready" ? "done" : "failed");
        if (result.status === "ready") refetch();
        return;
      }
      setAirbnbImportState("failed");
      toast.error("Import is taking longer than expected -- check back later or try fewer listings at once.");
    } catch (err) {
      setAirbnbImportState("failed");
      toast.error(err instanceof ApiError ? err.message : "Import failed to start");
    }
  }

  function resetAirbnbImportDialog() {
    setAirbnbUrlDialogOpen(false);
    setAirbnbUrlsText("");
    setAirbnbImportState("idle");
    setAirbnbImportResult(null);
  }

  async function handleSync(id: string) {
    setSyncingId(id);
    try {
      const result = await api.properties.syncIcal(id);
      toast.success(`iCal synced — ${result.created} created, ${result.updated} updated`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "iCal sync failed");
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap justify-end gap-4">
        <div className="flex flex-wrap gap-2">
          <input
            ref={importInputRef}
            type="file"
            accept="application/json"
            multiple
            className="hidden"
            onChange={handleImportFiles}
          />
          {/* Hidden 2026-10-09 -- Bright Data's "Customer is not active" account
              issue made this unreliable for hosts to hit live. Commented out,
              not deleted: the dialog/polling flow below is untouched, so this
              is a one-line revert once Bright Data's account status is
              confirmed stable again. */}
          <Button variant="outline" onClick={() => setAirbnbUrlDialogOpen(true)}>
            Import from Airbnb
          </Button>
          <Button variant="ghost" disabled={importing} onClick={() => importInputRef.current?.click()}>
            {importing ? "Importing…" : "Import from file (advanced)"}
          </Button>
          <RightPanel
            open={airbnbUrlDialogOpen}
            onOpenChange={(next) => (next ? setAirbnbUrlDialogOpen(true) : resetAirbnbImportDialog())}
            title="Import from Airbnb"
            size="lg"
            footer={
              airbnbImportState === "idle" || airbnbImportState === "triggering" ? (
                <RightPanelFooterButton
                  onClick={handleImportAirbnbUrls}
                  disabled={airbnbImportState === "triggering" || !airbnbUrlsText.trim()}
                >
                  {airbnbImportState === "triggering" ? "Starting import…" : "Import"}
                </RightPanelFooterButton>
              ) : airbnbImportState === "polling" ? undefined : (
                <RightPanelFooterButton onClick={resetAirbnbImportDialog}>Done</RightPanelFooterButton>
              )
            }
          >
            {airbnbImportState === "idle" || airbnbImportState === "triggering" ? (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  Paste your Airbnb listing links below, one per line. We&apos;ll pull each listing&apos;s details,
                  amenities, house rules, and neighborhood info automatically.
                </p>
                <Textarea
                  rows={6}
                  placeholder={"https://www.airbnb.com/rooms/12345678\nhttps://www.airbnb.com/rooms/87654321"}
                  value={airbnbUrlsText}
                  onChange={(e) => setAirbnbUrlsText(e.target.value)}
                  disabled={airbnbImportState === "triggering"}
                />
              </div>
            ) : airbnbImportState === "polling" ? (
              <div className="space-y-3 py-6 text-center">
                <Skeleton className="mx-auto h-6 w-48" />
                <p className="text-sm text-muted-foreground">
                  Importing {airbnbUrlCount} {airbnbUrlCount === 1 ? "listing" : "listings"} from Airbnb — this can take
                  a minute or two.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {airbnbImportResult?.results.length ? (
                  <div className="space-y-0">
                    {airbnbImportResult.results.map((r, i) => (
                      <ListRow key={i}>
                        <div className="flex min-w-0 items-center justify-between gap-3">
                          <span className="min-w-0 flex-1 truncate text-sm">{r.property?.name ?? r.filename}</span>
                          {r.status === "error" ? (
                            <StatusChip status="error" tone="destructive" />
                          ) : (
                            <StatusChip status={r.status} tone={r.status === "created" ? "live" : "progress"} />
                          )}
                        </div>
                        {r.status === "error" && r.error && (
                          <p className="text-xs break-words text-muted-foreground">{r.error}</p>
                        )}
                      </ListRow>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {airbnbImportResult?.error ??
                      "The import didn't return any listings — check the URLs and try again."}
                  </p>
                )}
              </div>
            )}
          </RightPanel>
          {isInternalOrg && (
            <>
              <Button variant="secondary" onClick={() => setTalkOpen(true)}>
                Test full portfolio in browser
              </Button>
              <TalkToMiraDialog open={talkOpen} onOpenChange={setTalkOpen} />
            </>
          )}
          <Button variant="cta" onClick={() => setOpen(true)}>
            Add property
          </Button>
          <RightPanel
            open={open}
            onOpenChange={setOpen}
            title="New property"
            size="xl"
            footer={
              <RightPanelFooterButton type="submit" form="create-property-form" disabled={submitting}>
                {submitting ? "Creating…" : "Create"}
              </RightPanelFooterButton>
            }
          >
            <form id="create-property-form" onSubmit={handleCreate} className="space-y-4">
              <Sheet>
                <PropertyFormFields form={form} onChange={setForm} />
              </Sheet>
              <Hint>Photos and FAQ can be added once the property is created.</Hint>
            </form>
          </RightPanel>
        </div>
      </div>

      {/* Only about the call intake number -- the one number guests dial to
          reach Mira. Gone as soon as one is saved (Exotel or Twilio). */}
      {user && !user.lead_exophone && !user.twilio_lead_number && !phoneBannerDismissed && (
        <div className="flex items-center justify-between gap-3 border border-foreground bg-card/70 px-4 py-3 text-sm">
          <p>
            Your call intake number isn&apos;t set — guests can&apos;t reach Mira until it is. Set it in{" "}
            <Link href="/dashboard/settings" className="font-medium underline underline-offset-2">
              Settings › Your account
            </Link>
            .
          </p>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setPhoneBannerDismissed(true)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      <Sheet>
        {!loading && properties && properties.length > 0 && (
          <Toolbar>
            <div className="w-full sm:w-64">
              <Input
                placeholder="Search properties…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                leadingIcon={<Search />}
              />
            </div>
          </Toolbar>
        )}

        {loading ? (
          [0, 1, 2].map((i) => (
            <Cell key={i} className="md:col-span-6 xl:col-span-4">
              <Skeleton className="h-48 w-full" />
            </Cell>
          ))
        ) : !properties || properties.length === 0 ? (
          <Cell>
            <p className="text-sm text-muted-foreground">No properties yet. Add one to get started.</p>
          </Cell>
        ) : filteredProperties.length === 0 ? (
          <Cell>
            <p className="text-sm text-muted-foreground">No properties match your search.</p>
          </Cell>
        ) : (
          filteredProperties.map((property) => (
            <Cell
              key={property.id}
              className="cursor-pointer gap-0 overflow-hidden p-0 md:col-span-6 xl:col-span-4"
              onClick={() => openEdit(property)}
            >
              <PropertyPhotoCarousel photos={property.photos} name={property.name} />
              <div className="flex flex-1 flex-col gap-2 border-t border-border p-5">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="min-w-0 text-[15px] leading-5 font-bold">{property.name}</h2>
                  {property.exophone && <StatusChip status="Voice agent live" tone="live" />}
                </div>
                <p className="text-[13px] text-muted-foreground">{property.city ?? "No city set"}</p>
                <p className="text-sm font-medium">
                  ₹{property.base_price.toLocaleString("en-IN")} / night · {property.max_guests} guests
                </p>
                <PolicyLabelChips overview={policies} propertyId={property.id} />
                {property.exophone && <p className="text-[13px] text-muted-foreground">{property.exophone}</p>}
                {perPropertyTransfer && !property.host_transfer_phone && (
                  <p className="text-[13px] text-(--status-pending-strong)" onClick={(e) => e.stopPropagation()}>
                    No transfer number. Transfers go to your main number.{" "}
                    <Link
                      href="/dashboard/settings#transfer-numbers"
                      className="font-medium underline underline-offset-2"
                    >
                      Set one
                    </Link>
                  </p>
                )}
              </div>
              <div
                className="flex items-center gap-2 border-t border-border px-5 py-3"
                onClick={(e) => e.stopPropagation()}
              >
                <Button size="sm" className="min-w-0 flex-1" onClick={() => handleSetDiscount(property.id)}>
                  Set Discount
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={
                      <Button variant="outline" size="icon-sm" className="shrink-0" aria-label="More actions">
                        <MoreHorizontal className="size-4" />
                      </Button>
                    }
                  />
                  <DropdownMenuContent align="end">
                    {isInternalOrg && (
                      <DropdownMenuItem onClick={() => handleTestInBrowser(property.id)}>Test</DropdownMenuItem>
                    )}
                    <DropdownMenuItem onClick={() => openEdit(property)}>Edit</DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={!property.ical_url || syncingId === property.id}
                      onClick={() => handleSync(property.id)}
                    >
                      {syncingId === property.id ? "Syncing…" : "Sync iCal"}
                    </DropdownMenuItem>
                    <DropdownMenuItem variant="destructive" onClick={() => handleDelete(property.id)}>
                      Remove
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </Cell>
          ))
        )}
      </Sheet>

      <RightPanel
        open={!!editing}
        onOpenChange={(isOpen) => !isOpen && setEditing(null)}
        title={`Edit ${editing?.name ?? ""}`}
        size="xl"
        footer={
          <RightPanelFooterButton type="submit" form="edit-property-form" disabled={savingEdit}>
            {savingEdit ? "Saving…" : "Save changes"}
          </RightPanelFooterButton>
        }
      >
        <form id="edit-property-form" onSubmit={handleSaveEdit} className="space-y-4">
          {/* One property, two ways to rent it: the Entire place tab is the
              property exactly as it always was; the Rooms tab is renting it
              room by room. Both save together with the one Save button. */}
          <Tabs key={editing?.id ?? "none"} defaultValue="entire">
            <TabsList>
              <TabsTrigger value="entire">Entire place</TabsTrigger>
              <TabsTrigger value="rooms">
                Rooms
                {editForm.room_rental_enabled && (
                  <span className="ml-1.5 inline-block size-1.5 rounded-full bg-(--status-live)" aria-label="On" />
                )}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="entire" className="pt-4">
              <Sheet>
                {editing && (
                  <PropertyFormSection
                    title="Photos"
                    count={(editForm.photos ?? []).length}
                    helpText="The first photo is the cover."
                  >
                    <PropertyPhotosManager
                      propertyId={editing.id}
                      photos={editForm.photos ?? []}
                      onChange={(photos) => setEditForm((f) => ({ ...f, photos }))}
                      propertyName={editing.name}
                    />
                  </PropertyFormSection>
                )}
                <PropertyFormFields form={editForm} onChange={setEditForm} />
                {editing && (
                  <PropertyPoliciesInEffect
                    overview={policies}
                    propertyId={editing.id}
                    onOpenPolicies={() => {
                      setEditing(null);
                      onOpenPolicies();
                    }}
                  />
                )}
                {editing &&
                  (loadingFaq ? (
                    <WideCell>
                      <div className="col-span-12 p-5">
                        <Skeleton className="h-24 w-full" />
                      </div>
                    </WideCell>
                  ) : (
                    <PropertyFaqManager propertyId={editing.id} entries={editFaqEntries} onChange={setEditFaqEntries} />
                  ))}
              </Sheet>
            </TabsContent>
            <TabsContent value="rooms" className="pt-4">
              <PropertyRoomRentalTab form={editForm} onChange={setEditForm} />
            </TabsContent>
          </Tabs>
        </form>
      </RightPanel>
    </div>
  );
}

const TABS = ["properties", "policies"] as const;
type PropertiesTab = (typeof TABS)[number];

function PropertiesPageTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requested = searchParams.get("tab");
  // The URL is the single source of truth, so a link to ?tab=policies from anywhere on this page works too.
  const tab: PropertiesTab = TABS.includes(requested as PropertiesTab) ? (requested as PropertiesTab) : "properties";
  // One fetch for the whole page so the tab can say how many policies are waiting for review.
  const policies = useAsync(() => api.policies.overview(), []);
  const toReview = policies.data?.drafts.length ?? 0;

  function handleTabChange(value: unknown) {
    if (typeof value !== "string" || !TABS.includes(value as PropertiesTab)) return;
    router.replace(`/dashboard/properties?tab=${value}`, { scroll: false });
  }

  return (
    <div className="space-y-6">
      <h1 className="page-title">Properties</h1>
      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="properties">Properties</TabsTrigger>
          <TabsTrigger value="policies">
            Policies
            {toReview > 0 && (
              <span className="ml-1.5 rounded-full bg-primary px-1.5 text-xs text-primary-foreground">{toReview}</span>
            )}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="properties" className="pt-4">
          <PropertiesList policies={policies.data} onOpenPolicies={() => handleTabChange("policies")} />
        </TabsContent>
        <TabsContent value="policies" className="pt-4">
          <PoliciesTab overview={policies.data} loading={policies.loading} onChanged={policies.refetch} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function PropertiesPage() {
  // useSearchParams requires a Suspense boundary.
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <PropertiesPageTabs />
    </Suspense>
  );
}
