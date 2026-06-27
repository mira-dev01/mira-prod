"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";

export default function FaqPage() {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const { data: entries, loading, refetch } = useAsync(() => api.faq.list(), []);

  const [propertyId, setPropertyId] = useState<string>("all");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const propertyName = (id: string | null) =>
    id ? properties?.find((p) => p.id === id)?.name ?? id : "All properties";

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.faq.create({
        property_id: propertyId === "all" ? null : propertyId,
        question,
        answer,
        category: category || null,
        status: "verified",
        verified_by: "host",
      });
      toast.success("FAQ entry added");
      setQuestion("");
      setAnswer("");
      setCategory("");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add FAQ entry");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleVerified(id: string, currentStatus: string) {
    try {
      await api.faq.update(id, { status: currentStatus === "verified" ? "pending" : "verified", verified_by: "host" });
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update FAQ entry");
    }
  }

  async function handleDelete(id: string) {
    try {
      await api.faq.remove(id);
      toast.success("FAQ entry removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove FAQ entry");
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">FAQ knowledge base</h1>
        <p className="text-sm text-muted-foreground">
          Verified answers the voice agent&apos;s search_faq tool can use — anything not verified here gets
          escalated to you instead of guessed.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add FAQ entry</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label>Applies to</Label>
              <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All properties</SelectItem>
                  {properties?.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="category">Category</Label>
              <Input id="category" placeholder="e.g. wifi, parking" value={category} onChange={(e) => setCategory(e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="question">Question</Label>
              <Input id="question" required value={question} onChange={(e) => setQuestion(e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="answer">Answer</Label>
              <Textarea id="answer" required value={answer} onChange={(e) => setAnswer(e.target.value)} />
            </div>
            <Button type="submit" className="sm:col-span-2" disabled={submitting}>
              Add (verified)
            </Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !entries || entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No FAQ entries yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Question</TableHead>
              <TableHead>Answer</TableHead>
              <TableHead>Applies to</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-40" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell className="max-w-[220px]">{entry.question}</TableCell>
                <TableCell className="max-w-[280px] truncate">{entry.answer}</TableCell>
                <TableCell>{propertyName(entry.property_id)}</TableCell>
                <TableCell>{entry.category ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant={entry.status === "verified" ? "secondary" : "outline"} className="capitalize">
                    {entry.status}
                  </Badge>
                </TableCell>
                <TableCell className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => handleToggleVerified(entry.id, entry.status)}>
                    {entry.status === "verified" ? "Unverify" : "Verify"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(entry.id)}>
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
