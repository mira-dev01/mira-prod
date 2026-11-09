"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { KeyRound, Users } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BillingTab } from "@/components/billing-tab";
import { TechniciansSection } from "@/components/settings/technicians-section";
import { FeaturesSection } from "@/components/settings/features-section";
import { DashboardSection } from "@/components/settings/dashboard-section";
import { Cell, CellLead, CellTitle, Hint, Sheet } from "@/components/ui/sheet";
import { CapabilityNotice } from "@/components/capability-notice";
import { HostCallHoursCard } from "@/components/settings/host-call-hours-card";
import { NotificationsCard } from "@/components/settings/notifications-card";
import { TransferNumbersCard } from "@/components/settings/transfer-numbers-card";
import { API_BASE_URL, ApiError, api, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const VALID_TABS = ["your account", "features", "dashboard", "technicians", "billing", "api", "team"] as const;
type SettingsTab = (typeof VALID_TABS)[number];

function ComingSoonTab({ icon: Icon, label }: { icon: React.ComponentType<{ className?: string }>; label: string }) {
  return (
    <Sheet>
      <Cell className="md:col-span-6 lg:col-span-4">
        <div className="flex items-center gap-2">
          <Icon className="size-4" />
          <CellTitle>{label}</CellTitle>
        </div>
        <Hint>Coming soon.</Hint>
      </Cell>
    </Sheet>
  );
}

/** One labelled fact in the profile strip. */
function Fact({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <Cell className={className} aria-label={label}>
      <CellTitle>{label}</CellTitle>
      <div className="min-w-0 text-[clamp(1.125rem,1.5vw,1.375rem)] leading-7 tracking-tight break-words">{children}</div>
    </Cell>
  );
}

function SettingsPageContent() {
  const { user, loading, refreshUser } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  // Derived from the URL rather than held in state, so in-page links (e.g.
  // Features & modules' "Add a technician" -> ?tab=technicians) switch tabs
  // too, not just the initial deep link.
  const tabParam = searchParams.get("tab");
  const tab: SettingsTab = VALID_TABS.includes(tabParam as SettingsTab) ? (tabParam as SettingsTab) : "your account";

  function handleTabChange(value: unknown) {
    if (typeof value !== "string" || !VALID_TABS.includes(value as SettingsTab)) return;
    // Keeps the URL deep-linkable without adding a history entry for every
    // tab click.
    router.replace(`/dashboard/settings?tab=${value}`, { scroll: false });
  }

  const [leadExophone, setLeadExophone] = useState(user?.lead_exophone ?? "");
  const [submitting, setSubmitting] = useState(false);


  async function handleSaveLeadExophone(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.auth.updateMe({ lead_exophone: leadExophone || null });
      await refreshUser();
      toast.success("Call intake number saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save call intake number");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTestLeadAgent() {
    const token = await getToken();
    if (!token) {
      toast.error("Not logged in");
      return;
    }
    const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
    const url = `${backendOrigin}/api/v1/voice/test?token=${encodeURIComponent(token)}`;
    window.open(url, "_blank");
  }

  const header = (
    <div className="flex flex-col gap-1">
      <h1 className="page-title">Settings</h1>
    </div>
  );

  if (loading) {
    return (
      <div className="space-y-6">
        {header}
        <Sheet>
          {[4, 4, 4].map((_, i) => (
            <Cell key={i} className="md:col-span-4">
              <Skeleton variant="text" className="w-1/2" />
              <Skeleton variant="text" />
              <Skeleton variant="text" className="w-3/4" />
            </Cell>
          ))}
        </Sheet>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {header}

      <Tabs value={tab} onValueChange={handleTabChange}>
        <div className="relative">
        <TabsList>
          <TabsTrigger value="your account">Your account</TabsTrigger>
          <TabsTrigger value="features">Features &amp; modules</TabsTrigger>
          <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
          <TabsTrigger value="technicians">Technicians</TabsTrigger>
          <TabsTrigger value="billing">Billing</TabsTrigger>
          <TabsTrigger value="api">API</TabsTrigger>
          <TabsTrigger value="team">Team</TabsTrigger>
        </TabsList>
        {/* Scroll cue: the tab row scrolls sideways on narrow screens. */}
        <span aria-hidden className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-linear-to-l from-background to-transparent lg:hidden" />
        </div>

        <TabsContent value="your account" className="pt-6">
          <Sheet>
            <Fact label="Name" className="col-span-12 sm:col-span-6 xl:col-span-2">
              {user?.name ?? "—"}
            </Fact>
            <Fact label="Email" className="col-span-12 sm:col-span-6 xl:col-span-4">
              <span title={user?.email}>{user?.email ?? "—"}</span>
            </Fact>
            <Fact label="Phone" className="col-span-12 sm:col-span-4 xl:col-span-2">
              {user?.phone ?? "—"}
            </Fact>
            <Fact label="Plan tier" className="col-span-6 sm:col-span-4 xl:col-span-2">
              <span className="capitalize">{user?.tier?.replace(/_/g, " ") ?? "—"}</span>
            </Fact>
            <Fact label="Status" className="col-span-6 sm:col-span-4 xl:col-span-2">
              <span className="inline-flex items-center gap-2 capitalize">
                <span
                  aria-hidden
                  className={user?.status === "active" ? "size-2 rounded-full bg-status-live" : "size-2 rounded-full bg-muted-foreground"}
                />
                {user?.status ?? "—"}
              </span>
            </Fact>

            <Cell className="md:col-span-6 lg:col-span-4">
              <CellTitle>Call intake number</CellTitle>
              <CellLead>The number guests call to reach Mira.</CellLead>
              <CapabilityNotice
                capabilityId="lead_agent"
                whenOff="The Portfolio Lead Agent is off in your features. Saving a number here turns it back on -- Mira answers whenever a call intake number is saved."
              />
              <form onSubmit={handleSaveLeadExophone} className="flex gap-2">
                <Input
                  aria-label="Call intake number"
                  placeholder="+9180XXXXXXXX"
                  value={leadExophone}
                  onChange={(e) => setLeadExophone(e.target.value)}
                />
                <Button type="submit" disabled={submitting}>
                  Save
                </Button>
              </form>
              <Button variant="outline" size="sm" className="self-start" onClick={handleTestLeadAgent}>
                Test Lead Agent in browser
              </Button>
            </Cell>

            <TransferNumbersCard />

            <HostCallHoursCard />

            <NotificationsCard />
          </Sheet>
        </TabsContent>

        <TabsContent value="features" className="pt-6">
          <FeaturesSection />
        </TabsContent>

        <TabsContent value="dashboard" className="pt-6">
          <DashboardSection />
        </TabsContent>

        <TabsContent value="technicians" className="pt-6">
          <TechniciansSection />
        </TabsContent>

        <TabsContent value="billing" className="pt-6">
          <BillingTab />
        </TabsContent>
        <TabsContent value="api" className="pt-6">
          <ComingSoonTab icon={KeyRound} label="API access" />
        </TabsContent>
        <TabsContent value="team" className="pt-6">
          <ComingSoonTab icon={Users} label="Team members" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function SettingsPage() {
  // useSearchParams requires a Suspense boundary -- see Next.js docs (this
  // matches the pattern already used on the Leads/Pricing pages).
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <SettingsPageContent />
    </Suspense>
  );
}
