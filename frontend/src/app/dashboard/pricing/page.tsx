"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionRow } from "@/components/ui/definition-row";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { PriceBreakdown } from "@/lib/types";

const RULE_TYPES = ["weekend_surge", "length_of_stay", "loyalty", "last_minute"];

export default function PricingPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <PricingPageContent />
    </Suspense>
  );
}

function PricingPageContent() {
  const searchParams = useSearchParams();
  const scopedPropertyId = searchParams.get("property_id");

  const { data: properties, loading: propertiesLoading } = useAsync(() => api.properties.list(), []);
  const { data: rules, loading: rulesLoading, refetch: refetchRules } = useAsync(() => api.pricing.rules(), []);
  const { data: discountRules } = useAsync(() => api.hostDiscountRules.list(), []);
  const approvedDiscountRulesCount = discountRules?.filter((r) => r.status === "approved").length ?? 0;

  const [ruleProperty, setRuleProperty] = useState<string>(scopedPropertyId ?? "");
  const [ruleType, setRuleType] = useState(RULE_TYPES[0]);
  const [discount, setDiscount] = useState(10);
  const [creatingRule, setCreatingRule] = useState(false);

  const [quoteProperty, setQuoteProperty] = useState<string>(scopedPropertyId ?? "");
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [numGuests, setNumGuests] = useState(2);
  const [quote, setQuote] = useState<PriceBreakdown | null>(null);
  const [quoting, setQuoting] = useState(false);

  const propertyName = (id: string) => properties?.find((p) => p.id === id)?.name ?? id;
  const scopedProperty = scopedPropertyId ? properties?.find((p) => p.id === scopedPropertyId) : undefined;

  // Pre-select the scoped property once the properties list has loaded, in
  // case the async fetch resolves after this component's initial render.
  useEffect(() => {
    if (!scopedPropertyId) return;
    setRuleProperty(scopedPropertyId);
    setQuoteProperty(scopedPropertyId);
  }, [scopedPropertyId]);

  const scopedRules = scopedPropertyId ? rules?.filter((r) => r.property_id === scopedPropertyId) : rules;

  async function handleCreateRule(e: React.FormEvent) {
    e.preventDefault();
    if (!ruleProperty) {
      toast.error("Pick a property first");
      return;
    }
    setCreatingRule(true);
    try {
      await api.pricing.createRule({ property_id: ruleProperty, rule_type: ruleType, discount_percent: discount });
      toast.success("Rule added");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create rule");
    } finally {
      setCreatingRule(false);
    }
  }

  async function handleDeleteRule(id: string) {
    try {
      await api.pricing.removeRule(id);
      toast.success("Rule removed");
      refetchRules();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove rule");
    }
  }

  async function handleQuote(e: React.FormEvent) {
    e.preventDefault();
    if (!quoteProperty || !checkIn || !checkOut) {
      toast.error("Fill in property and dates");
      return;
    }
    setQuoting(true);
    try {
      const result = await api.pricing.quote({
        property_id: quoteProperty,
        check_in: checkIn,
        check_out: checkOut,
        num_guests: numGuests,
      });
      setQuote(result);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to calculate quote");
    } finally {
      setQuoting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">{scopedPropertyId ? `Pricing — ${scopedProperty?.name ?? "…"}` : "Pricing"}</h1>
        <p className="text-sm text-muted-foreground">
          {scopedPropertyId
            ? "Set a discount for this property and preview rates, mirroring the get_pricing tool"
            : "Negotiation rules and rate preview, mirroring the get_pricing tool"}
        </p>
      </div>

      {(() => {
        const withSmartPrice = (properties ?? []).filter((p) => p.smart_price_estimate != null);
        if (withSmartPrice.length === 0) return null;
        return (
          <Card className="max-w-3xl">
            <CardHeader>
              <CardTitle>Smart pricing</CardTitle>
              <CardDescription>
                Median nightly rate across comparable live Airbnb listings in the same city, refreshed daily.
                Informational only — never applied automatically to quoted prices.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Property</TableHead>
                    <TableHead>Your price</TableHead>
                    <TableHead>Market price (Airbnb)</TableHead>
                    <TableHead>Comparables</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {withSmartPrice.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell>₹{p.base_price.toLocaleString("en-IN")}/night</TableCell>
                      <TableCell>₹{p.smart_price_estimate!.toLocaleString("en-IN")}/night</TableCell>
                      <TableCell className="text-muted-foreground">{p.smart_price_sample_size} listings in {p.city}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        );
      })()}

      {approvedDiscountRulesCount > 0 && (
        <Card className="max-w-2xl border-primary/30">
          <CardHeader>
            <CardTitle>Negotiation policy</CardTitle>
            <CardDescription>
              {approvedDiscountRulesCount} approved discount rule{approvedDiscountRulesCount === 1 ? "" : "s"} from
              your AI Training policy apply{approvedDiscountRulesCount === 1 ? "ies" : ""} to live negotiations across
              all your properties. These aren&apos;t listed as pricing rules below since they only affect what Mira
              offers when negotiating, not the standard quoted price.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/ai-training"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              Review negotiation policy
              <ArrowRight className="size-3.5" />
            </Link>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Pricing rules</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={handleCreateRule} className="grid grid-cols-2 gap-4">
              {!scopedPropertyId && (
                <div className="col-span-2 space-y-2">
                  <Label>Property</Label>
                  <Select value={ruleProperty} onValueChange={(v) => v && setRuleProperty(v)} disabled={propertiesLoading}>
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
              )}
              <div className="space-y-2">
                <Label>Rule type</Label>
                <Select value={ruleType} onValueChange={(v) => v && setRuleType(v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RULE_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t.replace("_", " ")}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Discount %</Label>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={discount}
                  onChange={(e) => setDiscount(Number(e.target.value))}
                />
              </div>
              <Button type="submit" className="col-span-2" disabled={creatingRule}>
                Add rule
              </Button>
            </form>

            {rulesLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : !scopedRules || scopedRules.length === 0 ? (
              <p className="text-sm text-muted-foreground">No pricing rules yet.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    {!scopedPropertyId && <TableHead>Property</TableHead>}
                    <TableHead>Type</TableHead>
                    <TableHead>Discount</TableHead>
                    <TableHead className="w-16" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {scopedRules.map((rule) => (
                    <TableRow key={rule.id}>
                      {!scopedPropertyId && <TableCell>{propertyName(rule.property_id)}</TableCell>}
                      <TableCell className="capitalize">{rule.rule_type.replace("_", " ")}</TableCell>
                      <TableCell>{rule.discount_percent}%</TableCell>
                      <TableCell>
                        <Button variant="ghost" size="sm" onClick={() => handleDeleteRule(rule.id)}>
                          Remove
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quote calculator</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={handleQuote} className="grid grid-cols-2 gap-4">
              {!scopedPropertyId && (
                <div className="col-span-2 space-y-2">
                  <Label>Property</Label>
                  <Select value={quoteProperty} onValueChange={(v) => v && setQuoteProperty(v)} disabled={propertiesLoading}>
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
              )}
              <div className="space-y-2">
                <Label>Check-in</Label>
                <Input type="date" value={checkIn} onChange={(e) => setCheckIn(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label>Check-out</Label>
                <Input type="date" value={checkOut} onChange={(e) => setCheckOut(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label>Guests</Label>
                <Input type="number" min={1} value={numGuests} onChange={(e) => setNumGuests(Number(e.target.value))} />
              </div>
              <Button type="submit" className="col-span-2 self-end" disabled={quoting}>
                Calculate
              </Button>
            </form>

            {quote && (
              <div className="space-y-1 rounded-md border p-4 text-sm">
                <DefinitionRow label="Nights" value={quote.nights} />
                <DefinitionRow label="Base total" value={`₹${quote.base_total.toLocaleString("en-IN")}`} />
                <DefinitionRow label="Weekend nights" value={quote.weekend_nights} />
                <DefinitionRow label="Cleaning fee" value={`₹${quote.cleaning_fee.toLocaleString("en-IN")}`} />
                <DefinitionRow label="Tax" value={`₹${quote.tax_amount.toLocaleString("en-IN")}`} />
                <DefinitionRow
                  label="Discount"
                  value={quote.discount_percent ? `${quote.discount_percent}% (-₹${quote.discount_amount.toLocaleString("en-IN")})` : "—"}
                />
                <div className="flex items-center justify-between pt-2 font-semibold">
                  <span>Total</span>
                  <Badge variant="secondary" className="text-base">
                    ₹{quote.total.toLocaleString("en-IN")}
                  </Badge>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
