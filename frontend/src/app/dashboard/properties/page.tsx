"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { PropertyFormFields } from "@/components/property-form-fields";
import { TalkToMiraDialog } from "@/components/talk-to-mira-dialog";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError, API_BASE_URL, getToken } from "@/lib/api";
import type { PropertyCreate, PropertyOut } from "@/lib/types";

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
  check_in_time: "14:00",
  check_out_time: "11:00",
  max_guests: 4,
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
    check_in_time: property.check_in_time,
    check_out_time: property.check_out_time,
    max_guests: property.max_guests,
  };
}

export default function PropertiesPage() {
  const { data: properties, loading, refetch } = useAsync(() => api.properties.list(), []);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<PropertyCreate>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [talkOpen, setTalkOpen] = useState(false);
  const [phoneBannerDismissed, setPhoneBannerDismissed] = useState(false);

  const [editing, setEditing] = useState<PropertyOut | null>(null);
  const [editForm, setEditForm] = useState<PropertyCreate>(emptyForm);
  const [savingEdit, setSavingEdit] = useState(false);

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

  function handleTestInBrowser(id: string) {
    const token = getToken();
    if (!token) {
      toast.error("Not logged in");
      return;
    }
    const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const url = `${backendOrigin}/api/v1/voice/test?property_id=${id}&token=${encodeURIComponent(token)}`;
    window.open(url, "_blank");
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
            `${totalFaqEntries} FAQ knowledge-base entries saved`
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
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Properties</h1>
          <p className="text-sm text-muted-foreground">Listings MIRA answers calls for</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={importInputRef}
            type="file"
            accept="application/json"
            multiple
            className="hidden"
            onChange={handleImportFiles}
          />
          <Button variant="outline" disabled={importing} onClick={() => importInputRef.current?.click()}>
            {importing ? "Importing…" : "Import from Airbnb"}
          </Button>
          <Button variant="secondary" onClick={() => setTalkOpen(true)}>
            Test full portfolio in browser
          </Button>
          <TalkToMiraDialog open={talkOpen} onOpenChange={setTalkOpen} />
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button>Add property</Button>} />
            <DialogContent className="max-h-[85vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>New property</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4">
                <PropertyFormFields form={form} onChange={setForm} />
                <DialogFooter>
                  <Button type="submit" disabled={submitting}>
                    Create
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {!loading && properties && properties.some((p) => !p.exophone) && !phoneBannerDismissed && (
        <div className="flex items-center justify-between gap-3 rounded-[var(--radius)] border bg-muted px-4 py-3 text-sm">
          <p>
            No phone numbers connected yet for these properties —{" "}
            <button
              type="button"
              className="font-medium underline underline-offset-2"
              onClick={() => {
                const firstMissing = properties.find((p) => !p.exophone);
                if (firstMissing) openEdit(firstMissing);
              }}
            >
              Connect ExoPhone
            </button>
          </p>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setPhoneBannerDismissed(true)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
      )}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-48 w-full" />
          ))}
        </div>
      ) : !properties || properties.length === 0 ? (
        <p className="text-sm text-muted-foreground">No properties yet — add one to get started.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {properties.map((property) => (
            <Card key={property.id}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  {property.name}
                  {property.exophone && (
                    <Badge variant="outline" className="badge-status-live">
                      Voice agent live
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{property.city ?? "No city set"}</p>
                <p>₹{property.base_price.toLocaleString("en-IN")} / night · {property.max_guests} guests</p>
                {property.exophone && <p className="text-muted-foreground">{property.exophone}</p>}
                <div className="flex items-center gap-2 pt-2">
                  <Button
                    size="sm"
                    className="flex-1"
                    onClick={() => handleTestInBrowser(property.id)}
                  >
                    Test
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button variant="outline" size="icon-sm" aria-label="More actions">
                          ⋯
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end">
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
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={!!editing} onOpenChange={(isOpen) => !isOpen && setEditing(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit {editing?.name}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSaveEdit} className="space-y-4">
            <PropertyFormFields form={editForm} onChange={setEditForm} />
            <DialogFooter>
              <Button type="submit" disabled={savingEdit}>
                Save changes
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
