"use client";

import { useState } from "react";
import { Phone, Star, Wrench, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";

const SPECIALTIES = ["plumbing", "electrical", "ac", "wifi", "lock", "general"];

// Moved out of its own /dashboard/technicians page into a Settings tab --
// same host-configuration category as the rest of Settings, not something
// checked day-to-day. Content unchanged from the original page, minus the
// page-level <h1> header (Settings' TabsTrigger label covers that now).
export function TechniciansSection() {
  const { data: properties, loading: propertiesLoading } = useAsync(() => api.properties.list(), []);
  const { data: technicians, loading, refetch } = useAsync(() => api.technicians.list(), []);

  const [propertyId, setPropertyId] = useState("");
  const [name, setName] = useState("");
  const [specialty, setSpecialty] = useState(SPECIALTIES[0]);
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const propertyName = (id: string) => properties?.find((p) => p.id === id)?.name ?? id;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!propertyId) {
      toast.error("Pick a property first");
      return;
    }
    setSubmitting(true);
    try {
      await api.technicians.create({ property_id: propertyId, name, specialty, phone });
      toast.success("Technician added");
      setName("");
      setPhone("");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add technician");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.technicians.remove(id);
      toast.success("Technician removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove technician");
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Local service providers MIRA can dispatch for physical issues (plumbing, electrical, AC, wifi, lock).
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Add technician</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <div className="space-y-2 lg:col-span-2">
              <Label>Property</Label>
              <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)} disabled={propertiesLoading}>
                <SelectTrigger>
                  <SelectValue placeholder="Select property">
                    {(value: string) => properties?.find((p) => p.id === value)?.name ?? "Select property"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {properties?.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>Specialty</Label>
              <Select value={specialty} onValueChange={(v) => v && setSpecialty(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SPECIALTIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Phone</Label>
              <Input value={phone} onChange={(e) => setPhone(e.target.value)} required />
            </div>
            <Button type="submit" className="lg:col-span-5" disabled={submitting}>
              Add technician
            </Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : !technicians || technicians.length === 0 ? (
        <p className="text-sm text-muted-foreground">No technicians on file yet.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {technicians.map((tech) => (
            <Card key={tech.id}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  <span className="min-w-0 truncate">{tech.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${tech.name}`}
                    onClick={() => handleDelete(tech.id)}
                    className="shrink-0 text-muted-foreground hover:text-destructive"
                  >
                    <X className="size-4" />
                  </button>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="flex items-center gap-1.5 text-muted-foreground">
                  <Wrench className="size-3.5 shrink-0" />
                  <span className="capitalize">{tech.specialty}</span>
                  <span aria-hidden="true">·</span>
                  <span>{propertyName(tech.property_id)}</span>
                </p>
                <p className="flex items-center gap-1.5 text-muted-foreground">
                  <Star className="size-3.5 shrink-0 fill-current text-(--status-pending)" />
                  {tech.rating.toFixed(1)}
                </p>
                <Button variant="outline" size="sm" className="mt-2 w-full" render={<a href={`tel:${tech.phone}`} />}>
                  <Phone className="size-3.5" />
                  {tech.phone}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
