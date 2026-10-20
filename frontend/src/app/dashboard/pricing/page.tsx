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
import { AiTrainingSection } from "@/components/settings/ai-training-section";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { BaselineStats, ObjectionTagStats, PriceBreakdown } from "@/lib/types";

const OBJECTION_TAG_LABELS: Record<string, string> = {
  PRICE_TOO_HIGH: "Price too high",
  DATES_UNAVAILABLE: "Dates unavailable",
  LOCATION_MISMATCH: "Location mismatch",
  AMENITY_MISSING: "Amenity missing",
  POLICY_MISMATCH: "Policy mismatch",
  HOST_UNRESPONSIVE: "Host unresponsive",
  GUEST_STOPPED_RESPONDING: "Guest stopped responding",
};

export default function PricingPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <PricingPageContent />
    </Suspense>
  );
}

// A rate computed off very few calls (e.g. 1/1 = 100%) looks like a strong
// signal but isn't one -- shown with an explicit sample-size caveat instead
// of a bare percentage, so a host doesn't read n=1 with the same confidence
// as n=50. No fixed "statistically significant" threshold is claimed; this
// is a plain low-confidence flag, not a p-value.
const LOW_SAMPLE_THRESHOLD = 5;

function ObjectionInsightRow({
  row,
  baseline,
}: {
  row: ObjectionTagStats;
  baseline: BaselineStats;
}) {
  const label = OBJECTION_TAG_LABELS[row.tag] ?? row.tag;
  const lowSample = row.total_calls < LOW_SAMPLE_THRESHOLD;
  const delta =
    row.conversion_rate != null && baseline.conversion_rate != null
      ? row.conversion_rate - baseline.conversion_rate
      : null;

  return (
    <TableRow>
      <TableCell>{label}</TableCell>
      <TableCell className="text-muted-foreground">{row.total_calls}</TableCell>
      <TableCell>
        {row.conversion_rate != null ? `${Math.round(row.conversion_rate * 100)}%` : "—"}
        {lowSample && (
          <span className="ml-1.5 text-xs text-muted-foreground">(low sample)</span>
        )}
      </TableCell>
      <TableCell className={delta != null && delta < 0 ? "text-destructive" : "text-muted-foreground"}>
        {baseline.conversion_rate != null ? `${Math.round(baseline.conversion_rate * 100)}% baseline` : "—"}
        {delta != null && (
          <span className="ml-1.5">
            ({delta >= 0 ? "+" : ""}
            {Math.round(delta * 100)}pp)
          </span>
        )}
      </TableCell>
    </TableRow>
  );
}

function PricingPageContent() {
  const searchParams = useSearchParams();
  const scopedPropertyId = searchParams.get("property_id");

  const { data: properties, loading: propertiesLoading } = useAsync(() => api.properties.list(), []);
  const { data: negotiationRules } = useAsync(() => api.negotiationRules.list(), []);
  const { data: objectionInsights } = useAsync(() => api.analytics.objectionInsights(), []);
  const approvedDiscountRulesCount =
    negotiationRules?.filter(
      (r) =>
        r.status === "approved" &&
        (r.rule_type === "discount_no_ask" ||
          r.rule_type === "discount_guest_requests" ||
          r.rule_type === "discount_repeat_guest")
    ).length ?? 0;

  const [quoteProperty, setQuoteProperty] = useState<string>(scopedPropertyId ?? "");
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [numGuests, setNumGuests] = useState(2);
  const [quote, setQuote] = useState<PriceBreakdown | null>(null);
  const [quoting, setQuoting] = useState(false);

  const scopedProperty = scopedPropertyId ? properties?.find((p) => p.id === scopedPropertyId) : undefined;

  // Pre-select the scoped property once the properties list has loaded, in
  // case the async fetch resolves after this component's initial render.
  useEffect(() => {
    if (!scopedPropertyId) return;
    setQuoteProperty(scopedPropertyId);
  }, [scopedPropertyId]);

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
          Rate preview, mirroring the get_pricing tool exactly — pulls the live Airbnb price via SearchApi for
          properties with exact Airbnb pricing enabled, falling back to the stored base rate otherwise.
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
                Median nightly rate across comparable live Airbnb listings in the same city, refreshed daily via
                SearchApi. Informational only — never applied automatically to quoted prices.
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

      {objectionInsights && objectionInsights.by_tag.length > 0 && (
        <Card className="max-w-3xl">
          <CardHeader>
            <CardTitle>Objection insights</CardTitle>
            <CardDescription>
              Conversion rate for calls where Mira identified this friction point, compared against your overall
              conversion rate. A correlation from past calls, not a recommendation — informational only, never
              applied automatically to pricing or negotiation.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Objection</TableHead>
                  <TableHead>Calls</TableHead>
                  <TableHead>Conversion rate</TableHead>
                  <TableHead>vs. baseline</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...objectionInsights.by_tag]
                  .sort((a, b) => b.total_calls - a.total_calls)
                  .slice(0, 3)
                  .map((row) => (
                    <ObjectionInsightRow key={row.tag} row={row} baseline={objectionInsights.baseline} />
                  ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {approvedDiscountRulesCount > 0 && (
        <Card className="max-w-2xl border-primary/30">
          <CardHeader>
            <CardTitle>Negotiation policy</CardTitle>
            <CardDescription>
              {approvedDiscountRulesCount} approved discount rule{approvedDiscountRulesCount === 1 ? "" : "s"} from
              your AI Training policy apply{approvedDiscountRulesCount === 1 ? "ies" : ""} to live negotiations across
              all your properties.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/properties/ai-training"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              Review negotiation policy
              <ArrowRight className="size-3.5" />
            </Link>
          </CardContent>
        </Card>
      )}

      <Card className="max-w-xl">
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

      <AiTrainingSection />
    </div>
  );
}
