"use client";

import { useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { PropertyCreate } from "@/lib/types";

const emptyForm: PropertyCreate = {
  name: "",
  city: "",
  exophone: "",
  base_price: 0,
  ical_url: "",
  house_rules: "",
  max_guests: 4,
};

export default function PropertiesPage() {
  const { data: properties, loading, refetch } = useAsync(() => api.properties.list(), []);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<PropertyCreate>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.properties.create(form);
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

  async function handleDelete(id: string) {
    try {
      await api.properties.remove(id);
      toast.success("Property removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove property");
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Properties</h1>
          <p className="text-sm text-muted-foreground">Listings MIRA answers calls for</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger render={<Button>Add property</Button>} />
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New property</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Name</Label>
                  <Input
                    id="name"
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="city">City</Label>
                  <Input id="city" value={form.city ?? ""} onChange={(e) => setForm({ ...form, city: e.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="exophone">ExoPhone</Label>
                  <Input
                    id="exophone"
                    placeholder="+9180XXXXXXXX"
                    value={form.exophone ?? ""}
                    onChange={(e) => setForm({ ...form, exophone: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="base_price">Base price (₹/night)</Label>
                  <Input
                    id="base_price"
                    type="number"
                    min={0}
                    required
                    value={form.base_price}
                    onChange={(e) => setForm({ ...form, base_price: Number(e.target.value) })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max_guests">Max guests</Label>
                  <Input
                    id="max_guests"
                    type="number"
                    min={1}
                    value={form.max_guests}
                    onChange={(e) => setForm({ ...form, max_guests: Number(e.target.value) })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ical_url">iCal URL</Label>
                  <Input
                    id="ical_url"
                    value={form.ical_url ?? ""}
                    onChange={(e) => setForm({ ...form, ical_url: e.target.value })}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="house_rules">House rules</Label>
                <Textarea
                  id="house_rules"
                  value={form.house_rules ?? ""}
                  onChange={(e) => setForm({ ...form, house_rules: e.target.value })}
                />
              </div>
              <DialogFooter>
                <Button type="submit" disabled={submitting}>
                  Create
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

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
                  {property.vapi_assistant_id ? (
                    <Badge variant="secondary">Voice agent live</Badge>
                  ) : (
                    <Badge variant="outline">No voice agent</Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{property.city ?? "No city set"}</p>
                <p>₹{property.base_price.toLocaleString("en-IN")} / night · {property.max_guests} guests</p>
                <p className="text-muted-foreground">{property.exophone ?? "No ExoPhone assigned"}</p>
                <div className="flex gap-2 pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!property.ical_url || syncingId === property.id}
                    onClick={() => handleSync(property.id)}
                  >
                    {syncingId === property.id ? "Syncing…" : "Sync iCal"}
                  </Button>
                  <Button variant="destructive" size="sm" onClick={() => handleDelete(property.id)}>
                    Remove
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
