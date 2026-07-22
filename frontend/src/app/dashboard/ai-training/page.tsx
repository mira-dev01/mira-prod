"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListRow, ListRowBody, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { HostDiscountRuleOut, HostDiscountRuleStatus } from "@/lib/types";

const TRIGGER_LABELS: Record<string, string> = {
  no_ask: "Guest doesn't ask for a discount",
  guest_requests: "Guest asks for a discount",
  repeat_guest_same_host: "Repeat guest across your properties",
};

function triggerLabel(triggerType: string): string {
  return TRIGGER_LABELS[triggerType] ?? triggerType;
}

function statusTone(status: string): "live" | "pending" | "destructive" {
  if (status === "approved") return "live";
  if (status === "rejected") return "destructive";
  return "pending";
}

export default function AiTrainingPage() {
  const { user, refreshUser } = useAuth();
  const [policyText, setPolicyText] = useState("");
  const [parsing, setParsing] = useState(false);

  const {
    data: rules,
    loading: rulesLoading,
    refetch: refetchRules,
  } = useAsync(() => api.hostDiscountRules.list(), []);

  const [editingRule, setEditingRule] = useState<HostDiscountRuleOut | null>(null);
  const [editDiscountPercent, setEditDiscountPercent] = useState("");
  const [savingRuleId, setSavingRuleId] = useState<string | null>(null);

  async function handleParsePolicy(e: React.FormEvent) {
    e.preventDefault();
    if (!policyText.trim()) return;
    setParsing(true);
    try {
      const result = await api.hostDiscountRules.parse(policyText);
      toast.success(
        `Found ${result.rules.length} discount rule${result.rules.length === 1 ? "" : "s"} -- review and approve below`
      );
      await refreshUser();
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not parse that discount policy");
    } finally {
      setParsing(false);
    }
  }

  function openEdit(rule: HostDiscountRuleOut) {
    setEditingRule(rule);
    setEditDiscountPercent(String(rule.discount_percent));
  }

  async function handleUpdateStatus(rule: HostDiscountRuleOut, status: HostDiscountRuleStatus) {
    setSavingRuleId(rule.id);
    try {
      await api.hostDiscountRules.update(rule.id, { status });
      toast.success(status === "approved" ? "Rule approved" : "Rule rejected");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update rule");
    } finally {
      setSavingRuleId(null);
    }
  }

  async function handleSaveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editingRule) return;
    const discountPercent = Number(editDiscountPercent);
    if (Number.isNaN(discountPercent) || discountPercent < 0 || discountPercent > 100) {
      toast.error("Discount must be a number between 0 and 100");
      return;
    }
    setSavingRuleId(editingRule.id);
    try {
      await api.hostDiscountRules.update(editingRule.id, {
        discount_percent: discountPercent,
        status: "approved",
      });
      toast.success("Rule updated and approved");
      setEditingRule(null);
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update rule");
    } finally {
      setSavingRuleId(null);
    }
  }

  async function handleRemoveRule(rule: HostDiscountRuleOut) {
    setSavingRuleId(rule.id);
    try {
      await api.hostDiscountRules.remove(rule.id);
      toast.success("Rule removed");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove rule");
    } finally {
      setSavingRuleId(null);
    }
  }

  const pendingRules = rules?.filter((r) => r.status === "pending_validation") ?? [];
  const decidedRules = rules?.filter((r) => r.status !== "pending_validation") ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">AI Training</h1>
        <p className="text-sm text-muted-foreground">
          Teach Mira how you handle discounts, and review anything she&apos;s learned before it goes live.
        </p>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Negotiation policy</CardTitle>
          <CardDescription>
            Describe how you usually handle discounts, in your own words -- e.g. &quot;If a guest doesn&apos;t
            ask, I keep the price as offered. If they ask for a discount, I offer 5%. Repeat guests across my
            properties get 8%.&quot; Mira will turn this into specific rules below for you to review and
            approve -- nothing changes until you approve it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleParsePolicy} className="space-y-3">
            <DictationTextarea
              placeholder="e.g. If a guest doesn't ask for a discount, I keep my price as offered. If they push back, I can go down to 5%. Guests who've stayed at more than one of my properties get 8% off."
              value={policyText}
              onValueChange={setPolicyText}
              className="min-h-32"
            />
            <Button type="submit" disabled={parsing || !policyText.trim()}>
              {parsing ? "Analyzing..." : "Analyze policy"}
            </Button>
          </form>
          {user?.discount_policy_text && (
            <p className="mt-3 text-xs text-muted-foreground">
              Last saved policy: &quot;{user.discount_policy_text}&quot;
            </p>
          )}
        </CardContent>
      </Card>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Pending validation</CardTitle>
          <CardDescription>
            Rules Mira drafted from your policy text above. Approve to make them live, edit the percentage
            first, or reject if it&apos;s wrong.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {rulesLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : pendingRules.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing waiting on review right now.</p>
          ) : (
            <div className="space-y-3">
              {pendingRules.map((rule) => (
                <ListRow key={rule.id} variant="boxed">
                  <ListRowHeader>
                    <span className="text-sm font-medium">{triggerLabel(rule.trigger_type)}</span>
                    <StatusChip status={rule.status.replace("_", " ")} tone={statusTone(rule.status)} />
                  </ListRowHeader>
                  <ListRowBody>
                    <p className="text-sm text-muted-foreground">Discount: {rule.discount_percent}%</p>
                  </ListRowBody>
                  <ListRowFooter className="gap-2">
                    <Button
                      size="sm"
                      disabled={savingRuleId === rule.id}
                      onClick={() => handleUpdateStatus(rule, "approved")}
                    >
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={savingRuleId === rule.id}
                      onClick={() => openEdit(rule)}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={savingRuleId === rule.id}
                      onClick={() => handleUpdateStatus(rule, "rejected")}
                    >
                      Reject
                    </Button>
                  </ListRowFooter>
                </ListRow>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Reviewed rules</CardTitle>
          <CardDescription>Approved rules are what Mira actually negotiates with today.</CardDescription>
        </CardHeader>
        <CardContent>
          {rulesLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : decidedRules.length === 0 ? (
            <p className="text-sm text-muted-foreground">No rules reviewed yet.</p>
          ) : (
            <div className="space-y-3">
              {decidedRules.map((rule) => (
                <ListRow key={rule.id} variant="divider">
                  <ListRowHeader>
                    <span className="text-sm font-medium">{triggerLabel(rule.trigger_type)}</span>
                    <StatusChip status={rule.status} tone={statusTone(rule.status)} />
                  </ListRowHeader>
                  <ListRowBody>
                    <p className="text-sm text-muted-foreground">
                      Discount: {rule.discount_percent}%
                      {rule.source === "host_edited" && " · edited by you"}
                    </p>
                  </ListRowBody>
                  <ListRowFooter className="gap-2">
                    <Button size="sm" variant="outline" onClick={() => openEdit(rule)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={savingRuleId === rule.id}
                      onClick={() => handleRemoveRule(rule)}
                    >
                      Remove
                    </Button>
                  </ListRowFooter>
                </ListRow>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {editingRule && (
        <Card className="max-w-2xl border-primary/40">
          <CardHeader>
            <CardTitle>Edit -- {triggerLabel(editingRule.trigger_type)}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSaveEdit} className="flex items-end gap-3">
              <div className="space-y-2">
                <Label htmlFor="discount-percent">Discount %</Label>
                <Input
                  id="discount-percent"
                  type="number"
                  min={0}
                  max={100}
                  value={editDiscountPercent}
                  onChange={(e) => setEditDiscountPercent(e.target.value)}
                  className="w-28"
                />
              </div>
              <Button type="submit" disabled={savingRuleId === editingRule.id}>
                Save and approve
              </Button>
              <Button type="button" variant="outline" onClick={() => setEditingRule(null)}>
                Cancel
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
