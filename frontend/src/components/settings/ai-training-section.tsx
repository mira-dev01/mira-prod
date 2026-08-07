"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListRow, ListRowBody, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { HostDiscountRuleOut, HostDiscountRuleStatus, PropertyPricingRuleOut } from "@/lib/types";

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

// Rendered on the standalone AI Training page under Properties
// (app/dashboard/properties/ai-training/page.tsx), alongside the merged-in
// Voice AI personalization form. Content unchanged from its earlier life as
// a Settings tab, minus the page-level <h1> header.
export function AiTrainingSection() {
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
        <h2 className="font-heading text-lg font-medium">AI Negotiation Policy</h2>
        <p className="text-sm text-muted-foreground">
          Teach Mira how you handle discounts, and review anything she&apos;s learned before it goes live.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-5 lg:items-start">
        <div className="lg:col-span-3">
          <Card>
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
        </div>

        <div className="space-y-6 lg:sticky lg:top-6 lg:col-span-2 lg:self-start">
          <Card>
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

          <Card>
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
            <Card className="border-primary/40">
              <CardHeader>
                <CardTitle>Edit -- {triggerLabel(editingRule.trigger_type)}</CardTitle>
              </CardHeader>
              <CardContent>
                <form onSubmit={handleSaveEdit} className="flex flex-wrap items-end gap-3">
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
      </div>
    </div>
  );
}

const PRICING_RULE_TYPE_LABELS: Record<string, string> = {
  length_of_stay: "Length-of-stay discount",
  minimum_stay_nights: "Minimum stay",
  early_checkin_fee: "Early check-in fee",
  late_checkout_fee: "Late checkout fee",
  custom: "Custom concession",
};

function pricingRuleTypeLabel(rule: PropertyPricingRuleOut): string {
  if (rule.rule_type === "custom" && rule.label) return rule.label;
  return PRICING_RULE_TYPE_LABELS[rule.rule_type] ?? rule.rule_type;
}

function pricingRuleConditionSummary(rule: PropertyPricingRuleOut): string {
  const c = rule.condition ?? {};
  if (rule.rule_type === "length_of_stay") {
    return `${rule.discount_percent ?? 0}% off for ${c.min_nights ?? "?"}+ nights`;
  }
  if (rule.rule_type === "minimum_stay_nights") {
    if (c.weekend_min_nights != null) return `${c.weekend_min_nights} nights minimum on weekends`;
    return `${c.min_nights ?? "?"} nights minimum`;
  }
  if (rule.rule_type === "early_checkin_fee" || rule.rule_type === "late_checkout_fee") {
    return `₹${c.fee ?? 0}`;
  }
  return rule.discount_percent != null ? `${rule.discount_percent}% off` : "—";
}

// Rendered on BOTH the AI Training page (app/dashboard/properties/ai-training)
// and the Pricing page (app/dashboard/pricing) -- same component, same
// list/parse/approve endpoints, so the two pages are wired together by
// construction rather than needing to be kept in sync by hand. The host can
// type or dictate a pricing ideology (DictationTextarea's existing mic ->
// POST /voice/transcribe -> Sarvam batch STT flow, unchanged, no new
// transcription code) -- Mira breaks it into draft rules, the host picks
// which properties each applies to via checkboxes, then approves/edits/
// rejects, same pending_validation -> approved lifecycle as the negotiation
// policy section above.
export function PricingRulesSection() {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const [policyText, setPolicyText] = useState("");
  const [parsing, setParsing] = useState(false);

  const {
    data: rules,
    loading: rulesLoading,
    refetch: refetchRules,
  } = useAsync(() => api.propertyPricingRules.list(), []);

  const [savingRuleId, setSavingRuleId] = useState<string | null>(null);
  const [selectedPropertyIds, setSelectedPropertyIds] = useState<Record<string, string[]>>({});

  async function handleParsePolicy(e: React.FormEvent) {
    e.preventDefault();
    if (!policyText.trim()) return;
    setParsing(true);
    try {
      const result = await api.propertyPricingRules.parse(policyText);
      toast.success(
        `Found ${result.rules.length} pricing rule${result.rules.length === 1 ? "" : "s"} -- pick which properties each applies to, then approve below`
      );
      setPolicyText("");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not parse that pricing policy");
    } finally {
      setParsing(false);
    }
  }

  function selectedFor(rule: PropertyPricingRuleOut): string[] {
    return selectedPropertyIds[rule.id] ?? rule.property_ids;
  }

  function togglePropertyForRule(rule: PropertyPricingRuleOut, propertyId: string, checked: boolean) {
    const current = selectedFor(rule);
    const next = checked ? [...current, propertyId] : current.filter((id) => id !== propertyId);
    setSelectedPropertyIds((prev) => ({ ...prev, [rule.id]: next }));
  }

  async function handleApprove(rule: PropertyPricingRuleOut) {
    const propertyIds = selectedFor(rule);
    if (propertyIds.length === 0) {
      toast.error("Select at least one property this rule applies to first");
      return;
    }
    setSavingRuleId(rule.id);
    try {
      await api.propertyPricingRules.update(rule.id, { status: "approved", property_ids: propertyIds });
      toast.success("Rule approved");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to approve rule");
    } finally {
      setSavingRuleId(null);
    }
  }

  async function handleUpdateStatus(rule: PropertyPricingRuleOut, status: "rejected") {
    setSavingRuleId(rule.id);
    try {
      await api.propertyPricingRules.update(rule.id, { status });
      toast.success("Rule rejected");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update rule");
    } finally {
      setSavingRuleId(null);
    }
  }

  async function handleRemoveRule(rule: PropertyPricingRuleOut) {
    setSavingRuleId(rule.id);
    try {
      await api.propertyPricingRules.remove(rule.id);
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
        <h2 className="font-heading text-lg font-medium">Pricing rules</h2>
        <p className="text-sm text-muted-foreground">
          Describe or dictate your pricing ideology -- minimum-stay requirements, length-of-stay discounts, early
          check-in/late checkout fees, or any other concession -- and pick which properties each rule applies to.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-5 lg:items-start">
        <div className="lg:col-span-3">
          <Card>
            <CardHeader>
              <CardTitle>Pricing policy</CardTitle>
              <CardDescription>
                Type it or use the mic to dictate -- e.g. &quot;Saturdays need a 2-night minimum. I offer 10% off for
                stays over 5 nights. Early check-in is an extra 1,500 rupees.&quot; Mira will turn this into specific
                rules below for you to review, scope to properties, and approve.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleParsePolicy} className="space-y-3">
                <DictationTextarea
                  placeholder="e.g. Saturdays need a 2-night minimum. 10% off for stays over 5 nights. Early check-in is an extra 1,500 rupees."
                  value={policyText}
                  onValueChange={setPolicyText}
                  className="min-h-32"
                />
                <Button type="submit" disabled={parsing || !policyText.trim()}>
                  {parsing ? "Analyzing..." : "Analyze policy"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6 lg:sticky lg:top-6 lg:col-span-2 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Pending validation</CardTitle>
              <CardDescription>
                Select which properties each rule applies to, then approve -- nothing changes until you do.
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
                        <span className="text-sm font-medium">{pricingRuleTypeLabel(rule)}</span>
                        <StatusChip status={rule.status.replace("_", " ")} tone="pending" />
                      </ListRowHeader>
                      <ListRowBody>
                        <p className="text-sm text-muted-foreground">{pricingRuleConditionSummary(rule)}</p>
                        <div className="mt-2 space-y-1">
                          {properties?.map((property) => (
                            <label key={property.id} className="flex items-center gap-2 text-sm">
                              <Checkbox
                                checked={selectedFor(rule).includes(property.id)}
                                onCheckedChange={(checked) =>
                                  togglePropertyForRule(rule, property.id, checked === true)
                                }
                              />
                              {property.name}
                            </label>
                          ))}
                        </div>
                      </ListRowBody>
                      <ListRowFooter className="gap-2">
                        <Button size="sm" disabled={savingRuleId === rule.id} onClick={() => handleApprove(rule)}>
                          Approve
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

          <Card>
            <CardHeader>
              <CardTitle>Reviewed rules</CardTitle>
              <CardDescription>Approved rules are what Mira actually applies to pricing today.</CardDescription>
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
                        <span className="text-sm font-medium">{pricingRuleTypeLabel(rule)}</span>
                        <StatusChip
                          status={rule.status}
                          tone={rule.status === "approved" ? "live" : "destructive"}
                        />
                      </ListRowHeader>
                      <ListRowBody>
                        <p className="text-sm text-muted-foreground">
                          {pricingRuleConditionSummary(rule)}
                          {rule.status === "approved" && ` · ${rule.property_ids.length} propert${rule.property_ids.length === 1 ? "y" : "ies"}`}
                        </p>
                      </ListRowBody>
                      <ListRowFooter className="gap-2">
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
        </div>
      </div>
    </div>
  );
}
