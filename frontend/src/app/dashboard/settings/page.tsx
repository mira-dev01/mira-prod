"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_BASE_URL, ApiError, api, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SettingsPage() {
  const { user, refreshUser } = useAuth();
  const [leadExophone, setLeadExophone] = useState(user?.lead_exophone ?? "");
  const [submitting, setSubmitting] = useState(false);

  async function handleSaveLeadExophone(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.auth.updateMe({ lead_exophone: leadExophone || null });
      await refreshUser();
      toast.success("Lead intake number saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save lead intake number");
    } finally {
      setSubmitting(false);
    }
  }

  function handleTestLeadAgent() {
    const token = getToken();
    if (!token) {
      toast.error("Not logged in");
      return;
    }
    const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const url = `${backendOrigin}/api/v1/voice/test?token=${encodeURIComponent(token)}`;
    window.open(url, "_blank");
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Settings</h1>
        <p className="text-sm text-muted-foreground">Account details</p>
      </div>

      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>Host account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Row label="Name" value={user?.name ?? "—"} />
          <Row label="Email" value={user?.email ?? "—"} />
          <Row label="Phone" value={user?.phone ?? "—"} />
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Plan tier</span>
            <Badge variant="secondary" className="capitalize">
              {user?.tier ?? "—"}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Status</span>
            <Badge variant="outline" className="capitalize">
              {user?.status ?? "—"}
            </Badge>
          </div>
        </CardContent>
      </Card>

      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>Lead intake number</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-muted-foreground">
            Calls to this number run the Lead Agent across your full property portfolio instead of one
            property — for booking enquiries, not existing-guest support.
          </p>
          <form onSubmit={handleSaveLeadExophone} className="flex gap-2">
            <Input
              placeholder="+9180XXXXXXXX"
              value={leadExophone}
              onChange={(e) => setLeadExophone(e.target.value)}
            />
            <Button type="submit" disabled={submitting}>
              Save
            </Button>
          </form>
          <Button variant="secondary" size="sm" className="mt-3" onClick={handleTestLeadAgent}>
            Test Lead Agent in browser
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}
