"use client";

import { useState } from "react";
import { Phone, Star, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Cell, CellLead, Sheet, SheetRow, WideCell } from "@/components/ui/sheet";
import { CapabilityNotice } from "@/components/capability-notice";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PropertyCombobox } from "@/components/property-combobox";
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
    <Sheet>
      <Cell className="gap-3">
        <CellLead>Service providers Mira sends for physical issues.</CellLead>
        <CapabilityNotice
          capabilityId="technician_dispatch"
          whenOff="Technician dispatch is off, so Mira won't suggest anyone from this list to guests -- she flags maintenance issues to you instead. Your technicians are kept for when you turn it back on."
        />
      </Cell>

      <WideCell>
        <SheetRow title="Add technician">
          <form onSubmit={handleCreate} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-2 sm:col-span-2">
              <Label>Property</Label>
              <PropertyCombobox
                properties={properties ?? []}
                value={propertyId}
                onChange={setPropertyId}
                disabled={propertiesLoading}
                placeholder="Search property"
              />
            </div>
            <div className="space-y-2 sm:col-span-2 lg:col-span-1">
              <Label>Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>Specialty</Label>
              <Select value={specialty} onValueChange={(v) => v && setSpecialty(v)}>
                <SelectTrigger className="w-full">
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
            <div className="flex items-end sm:col-span-2 lg:col-span-1">
              <Button type="submit" className="w-full" disabled={submitting}>
                Add technician
              </Button>
            </div>
          </form>
        </SheetRow>
      </WideCell>

      <WideCell>
        <SheetRow title="On file">
          {loading ? (
            <Skeleton className="h-24 w-full" />
          ) : !technicians || technicians.length === 0 ? (
            <p className="text-sm text-muted-foreground">No technicians on file yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Specialty</TableHead>
                  <TableHead>Property</TableHead>
                  <TableHead>Rating</TableHead>
                  <TableHead>Phone</TableHead>
                  <TableHead className="w-10">
                    <span className="sr-only">Remove</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {technicians.map((tech) => (
                  <TableRow key={tech.id}>
                    <TableCell className="font-semibold">{tech.name}</TableCell>
                    <TableCell className="capitalize">{tech.specialty}</TableCell>
                    <TableCell>{propertyName(tech.property_id)}</TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">
                        <Star className="size-3.5 shrink-0 fill-current text-(--status-pending)" />
                        {tech.rating.toFixed(1)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <a href={`tel:${tech.phone}`} className="inline-flex items-center gap-1.5 font-medium text-foreground underline underline-offset-2 hover:no-underline">
                        <Phone className="size-3.5" />
                        {tech.phone}
                      </a>
                    </TableCell>
                    <TableCell>
                      <button
                        type="button"
                        aria-label={`Remove ${tech.name}`}
                        onClick={() => handleDelete(tech.id)}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <X className="size-4" />
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </SheetRow>
      </WideCell>
    </Sheet>
  );
}
