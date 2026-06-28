"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";

const SPECIALTIES = ["plumbing", "electrical", "ac", "wifi", "lock", "general"];

export default function TechniciansPage() {
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
      <div>
        <h1 className="page-title">Technicians</h1>
        <p className="text-sm text-muted-foreground">Local service providers MIRA can dispatch for physical issues</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add technician</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="space-y-2 lg:col-span-2">
              <Label>Property</Label>
              <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)} disabled={propertiesLoading}>
                <SelectTrigger>
                  <SelectValue placeholder="Select property" />
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
        <Skeleton className="h-48 w-full" />
      ) : !technicians || technicians.length === 0 ? (
        <p className="text-sm text-muted-foreground">No technicians on file yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Property</TableHead>
              <TableHead>Specialty</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Rating</TableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {technicians.map((tech) => (
              <TableRow key={tech.id}>
                <TableCell>{tech.name}</TableCell>
                <TableCell>{propertyName(tech.property_id)}</TableCell>
                <TableCell className="capitalize">{tech.specialty}</TableCell>
                <TableCell>{tech.phone}</TableCell>
                <TableCell>{tech.rating.toFixed(1)}</TableCell>
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(tech.id)}>
                    Remove
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
