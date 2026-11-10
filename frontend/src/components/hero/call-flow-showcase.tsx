"use client";

import { useEffect, useState } from "react";
import { Cell, CellTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

// Looping "Mira at work" storyboard: one guest call told in seven steps. The
// active step is highlighted and the loop advances on a fixed interval, so the
// panel reads as a single call in stages. 7 steps x 2s = 14s per loop.
const STEPS = [
  { key: "ringing", label: "Incoming call", title: "Guest calling…", body: "+91 98765 43210" },
  { key: "answering", label: "Mira answering", title: "“Can I check in early, around 11am?”", body: "Goa Villa · guest enquiry" },
  { key: "searching", label: "Checking property info", title: "Looking up check-in policy", body: "Calendar + house rules · Goa Villa" },
  { key: "answer", label: "Answer generated", title: "“Yes, 11am works — no extra charge.”", body: "Mira replies instantly" },
  { key: "qualified", label: "Booking confirmed", title: "Guest confirms booking", body: "3 nights · 2 guests · Goa Villa" },
  { key: "whatsapp", label: "WhatsApp summary sent", title: "Host notified", body: "“Early check-in — approved by Mira”" },
  { key: "dashboard", label: "Lead added", title: "Booking appears on dashboard", body: "New enquiry · Goa Villa · Qualified" },
] as const;

const STEP_DURATION_MS = 2000;

export function CallFlowShowcase({ className }: { className?: string }) {
  const [stepIndex, setStepIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setStepIndex((i) => (i + 1) % STEPS.length), STEP_DURATION_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <Cell className={cn("gap-0 p-0", className)}>
      <div className="border-b border-border px-5 py-4">
        <CellTitle>One call, start to finish</CellTitle>
      </div>
      <ol>
        {STEPS.map((step, i) => {
          const active = i === stepIndex;
          return (
            <li
              key={step.key}
              aria-current={active ? "step" : undefined}
              className={cn(
                "border-b border-border px-5 py-3 transition-colors duration-300 last:border-0 motion-reduce:transition-none",
                active ? "bg-accent text-foreground" : "text-muted-foreground"
              )}
            >
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-[13px] font-bold tracking-[0.1em] uppercase">{step.label}</p>
                <span className="text-[13px] tabular-nums">{i + 1}</span>
              </div>
              <p className="font-heading text-[15px] leading-snug font-semibold">{step.title}</p>
              {active && <p className="text-[13px] text-muted-foreground">{step.body}</p>}
            </li>
          );
        })}
      </ol>
    </Cell>
  );
}
