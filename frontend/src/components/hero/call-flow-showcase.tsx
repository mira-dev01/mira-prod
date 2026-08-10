"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Phone,
  PhoneCall,
  Search,
  MessageSquareText,
  CheckCircle2,
  MessageCircle,
  LayoutDashboard,
} from "lucide-react";
import { DashboardMockup } from "@/components/hero/dashboard-mockup";

// Looping "Mira at work" storyboard for the hero showcase. Each step owns a
// floating card; steps cross-fade on a fixed interval so the panel reads as
// one continuous guest call told in stages, not a busy dashboard replica.
// 7 steps x 2s = 14s per loop, inside the brief's 12-15s window.
const STEPS = [
  {
    key: "ringing",
    icon: Phone,
    iconColorVar: "--status-pending",
    label: "Incoming call",
    title: "Guest calling…",
    body: "+91 98765 43210",
  },
  {
    key: "answering",
    icon: PhoneCall,
    iconColorVar: "--status-live",
    label: "Mira answering",
    title: "“Can I check in early, around 11am?”",
    body: "Goa Villa · guest enquiry",
  },
  {
    key: "searching",
    icon: Search,
    iconColorVar: "--status-progress",
    label: "Checking property info",
    title: "Looking up check-in policy",
    body: "Calendar + house rules · Goa Villa",
  },
  {
    key: "answer",
    icon: MessageSquareText,
    iconColorVar: "--status-progress",
    label: "Answer generated",
    title: "“Yes, 11am works — no extra charge.”",
    body: "Mira replies instantly",
  },
  {
    key: "qualified",
    icon: CheckCircle2,
    iconColorVar: "--status-live",
    label: "Booking confirmed",
    title: "Guest confirms booking",
    body: "3 nights · 2 guests · Goa Villa",
  },
  {
    key: "whatsapp",
    icon: MessageCircle,
    iconColorVar: "--chart-2",
    label: "WhatsApp summary sent",
    title: "Host notified",
    body: "“Early check-in — approved by Mira”",
  },
  {
    key: "dashboard",
    icon: LayoutDashboard,
    iconColorVar: "--chart-5",
    label: "Lead added",
    title: "Booking appears on dashboard",
    body: "New enquiry · Goa Villa · Qualified",
  },
] as const;

const STEP_DURATION_MS = 2000;

export function CallFlowShowcase() {
  const [stepIndex, setStepIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setStepIndex((i) => (i + 1) % STEPS.length);
    }, STEP_DURATION_MS);
    return () => clearInterval(id);
  }, []);

  const step = STEPS[stepIndex];
  const Icon = step.icon;

  return (
    <div className="relative mx-auto flex h-[440px] w-full max-w-lg flex-col items-center pt-6">
      {/* Faint radial glow + grid, contained to this panel only. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-3xl"
        style={{
          background:
            "radial-gradient(ellipse at 50% 30%, color-mix(in oklch, var(--accent-warm) 16%, transparent), transparent 65%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-3xl opacity-[0.3]"
        style={{
          backgroundImage:
            "linear-gradient(to right, color-mix(in oklch, var(--foreground) 5%, transparent) 1px, transparent 1px), linear-gradient(to bottom, color-mix(in oklch, var(--foreground) 5%, transparent) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
          maskImage: "radial-gradient(ellipse at center, black 40%, transparent 75%)",
        }}
      />

      {/* Step progress dots */}
      <div className="relative z-10 mb-6 flex items-center gap-1.5">
        {STEPS.map((s, i) => (
          <span
            key={s.key}
            className="h-1.5 rounded-full transition-all duration-500"
            style={{
              width: i === stepIndex ? "1.25rem" : "0.375rem",
              backgroundColor: i === stepIndex ? "var(--primary)" : "var(--border)",
            }}
          />
        ))}
      </div>

      {/* Blurred dashboard mockup, sitting behind the workflow cards to give
          the panel depth without competing with the animated narrative. */}
      <div className="relative h-[340px] w-full max-w-md">
        <motion.div
          className="absolute inset-6"
          animate={{ y: [0, -8, 0] }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        >
          <DashboardMockup />
        </motion.div>

        <AnimatePresence mode="wait">
          <motion.div
            key={step.key}
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.98 }}
            transition={{ duration: 0.55, ease: [0.4, 0, 0.2, 1] }}
            className="absolute inset-x-6 top-20 rounded-2xl bg-card p-6 ring-1 ring-foreground/10"
            style={{ boxShadow: "var(--shadow-xl)" }}
          >
            <div className="flex items-center gap-3">
              <span
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                style={{
                  backgroundColor: `color-mix(in oklch, var(${step.iconColorVar}), transparent 88%)`,
                }}
              >
                <Icon className="h-5 w-5" style={{ color: `var(${step.iconColorVar})` }} />
              </span>
              <span className="text-micro">{step.label}</span>
            </div>
            <p className="mt-4 font-heading text-lg leading-snug font-medium text-card-foreground">
              {step.title}
            </p>
            <p className="mt-1.5 text-sm text-muted-foreground">{step.body}</p>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
