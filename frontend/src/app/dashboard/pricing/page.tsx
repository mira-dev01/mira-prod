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
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { PriceBreakdown } from "@/lib/types";

const RULE_TYPES = ["weekend_surge", "length_of_stay", "loyalty", "last_minute"];

export default function PricingPage() {
  const { data: properties, loading: propertiesLoading } = useAsync(() => api.properties.list(), []);
  const { data: rules, loading: rulesLoading, refetch: refetchRules } = useAsync(() => api.pricing.rules(), []);

  const [ruleProperty, setRuleProperty] = useState<string>("");
  const [ruleType, setRuleType] = useState(RULE_TYPES[0]);
  const [discount, setDiscount] = useState(10);
  const [creatingRule, setCreatingRule] = useState(false);

  const [quoteProperty, setQuoteProperty] = useState<string>("");
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [numGuests, setNumGuests] = useState(2);
  const [quote, setQuote] = useState<PriceBreakdown | null>(null);
  const [quoting, setQuoting] = useState(false);

  const propertyName = (id: string) => properties?.find((p) => p.id === id)?.name ?? id;

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
        <h1 className="text-2xl font-semibold tracking-tight">Pricing</h1>
        <p className="text-sm text-muted-foreground">Negotiation rules and rate preview, mirroring the get_pricing tool</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Pricing rules</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={handleCreateRule} className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-2">
                <Label>Property</Label>
                <Select value={ruleProperty} onValueChange={(v) => v && setRuleProperty(v)} disabled={propertiesLoading}>
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
            ) : !rules || rules.length === 0 ? (
              <p className="text-sm text-muted-foreground">No pricing rules yet.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Property</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Discount</TableHead>
                    <TableHead className="w-16" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rules.map((rule) => (
                    <TableRow key={rule.id}>
                      <TableCell>{propertyName(rule.property_id)}</TableCell>
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
            <form onSubmit={handleQuote} className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-2">
                <Label>Property</Label>
                <Select value={quoteProperty} onValueChange={(v) => v && setQuoteProperty(v)} disabled={propertiesLoading}>
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
                <Row label="Nights" value={quote.nights} />
                <Row label="Base total" value={`₹${quote.base_total.toLocaleString("en-IN")}`} />
                <Row label="Weekend nights" value={quote.weekend_nights} />
                <Row label="Cleaning fee" value={`₹${quote.cleaning_fee.toLocaleString("en-IN")}`} />
                <Row label="Tax" value={`₹${quote.tax_amount.toLocaleString("en-IN")}`} />
                <Row
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

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}
