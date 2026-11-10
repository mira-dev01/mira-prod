"use client";

import Link from "next/link";
import { MiraLogo } from "@/components/brand/mira-logo";
import { Button } from "@/components/ui/button";
import { CallFlowShowcase } from "@/components/hero/call-flow-showcase";
import { Cell, Sheet } from "@/components/ui/sheet";
import type { UserOut } from "@/lib/types";

const PROMISES = ["AI answers instantly", "Available 24/7", "WhatsApp summaries", "Human handoff when needed"];

export function LandingHero({ user }: { user: UserOut | null }) {
  return (
    <section className="mx-auto flex min-h-screen w-full max-w-[1200px] flex-col gap-8 px-4 py-6 md:px-8 md:py-10">
      <header>
        <MiraLogo className="h-9 text-foreground" />
      </header>

      <Sheet className="content-start">
        <Cell className="justify-center gap-8 p-6 md:p-10 lg:col-span-7">
          <h1 className="font-heading text-[2.5rem] leading-[1.05] font-bold tracking-tight text-balance sm:text-6xl">
            Stop answering calls.
            <br />
            Start closing bookings.
          </h1>
          <p className="max-w-lg text-lg leading-relaxed text-muted-foreground">
            Mira answers every guest call, so you get your time back and every enquiry still becomes a booking.
          </p>
          <div className="flex flex-wrap items-center gap-5">
            {user ? (
              <Button variant="cta" size="lg" nativeButton={false} className="h-12 px-7 text-base" render={<Link href="/dashboard" />}>
                Open Mira
              </Button>
            ) : (
              <>
                <Button
                  variant="cta"
                  size="lg"
                  nativeButton={false}
                  className="h-12 px-7 text-base"
                  render={<Link href="/login?intent=demo" />}
                >
                  Sign in
                </Button>
                <Link href="/login?tab=register" className="text-sm font-semibold underline underline-offset-4 hover:no-underline">
                  Register
                </Link>
              </>
            )}
          </div>
        </Cell>

        <CallFlowShowcase className="lg:col-span-5" />

        {PROMISES.map((label) => (
          <Cell key={label} className="col-span-6 p-4 sm:p-5 lg:col-span-3">
            <p className="text-sm font-semibold">{label}</p>
          </Cell>
        ))}
      </Sheet>
    </section>
  );
}
