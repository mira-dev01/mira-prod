"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Zap, PhoneCall, MessageCircle, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CallFlowShowcase } from "@/components/hero/call-flow-showcase";
import { ScrollCue } from "@/components/hero/scroll-cue";
import type { UserOut } from "@/lib/types";

const TRUST_INDICATORS = [
  { icon: Zap, label: "AI answers instantly" },
  { icon: PhoneCall, label: "Available 24/7" },
  { icon: MessageCircle, label: "WhatsApp summaries" },
  { icon: Users, label: "Human handoff when needed" },
];

const EASE = [0.4, 0, 0.2, 1] as const;

export function LandingHero({ user }: { user: UserOut | null }) {
  return (
    <section className="relative isolate flex min-h-screen flex-col overflow-hidden">

      <div className="mx-auto grid w-full max-w-[1440px] flex-1 grid-cols-1 items-center gap-16 px-6 py-20 md:px-12 lg:grid-cols-[45fr_55fr] lg:gap-8 lg:py-24">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: EASE }}
        >
          <span className="text-micro">MIRA — AI Receptionist for Airbnb Hosts</span>

          <h1
            className="mt-6 text-[2.75rem] leading-[1.05] font-normal text-balance sm:text-6xl lg:text-[4.75rem]"
            // Clash Display (--font-display) has no italic cut, unlike Boska
            // before it -- left upright rather than a synthesized fake-oblique.
            style={{ fontFamily: "var(--font-display)", letterSpacing: "-1px" }}
          >
            Stop answering calls.
            <br />
            Start closing bookings.
          </h1>

          <p className="mt-8 max-w-lg text-lg leading-relaxed text-muted-foreground">
            Mira takes every guest call off your plate — so you get your time back, and every
            enquiry still converts into a booking.
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-5">
            {user ? (
              <Button
                size="lg"
                nativeButton={false}
                className="h-12 px-7 text-base transition-transform duration-200 hover:-translate-y-0.5 hover:shadow-lg"
                render={<Link href="/dashboard" />}
              >
                Open Mira
              </Button>
            ) : (
              <>
                <Button
                  size="lg"
                  nativeButton={false}
                  className="h-12 px-7 text-base transition-transform duration-200 hover:-translate-y-0.5 hover:shadow-lg"
                  render={<Link href="/login?intent=demo" />}
                >
                  Login
                </Button>
                <Link
                  href="/login?tab=register"
                  className="text-sm font-medium text-primary underline-offset-4 hover:underline"
                >
                  Register
                </Link>
              </>
            )}
          </div>

          <ul className="mt-14 grid grid-cols-1 gap-3.5 sm:grid-cols-2">
            {TRUST_INDICATORS.map(({ icon: Icon, label }) => (
              <li key={label} className="flex items-center gap-2.5 text-sm text-muted-foreground">
                <Icon className="size-4 shrink-0" style={{ color: "var(--accent-warm)" }} />
                {label}
              </li>
            ))}
          </ul>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.15, ease: EASE }}
        >
          <CallFlowShowcase />
        </motion.div>
      </div>

      <ScrollCue />

      {/* Fade the hero into whatever follows instead of ending abruptly. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-24"
        style={{ background: "linear-gradient(to bottom, transparent, var(--background))" }}
      />
    </section>
  );
}
