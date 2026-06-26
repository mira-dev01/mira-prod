"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
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
