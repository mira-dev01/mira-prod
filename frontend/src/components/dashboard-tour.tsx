"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { TOUR_EVENT, TOUR_STEPS, markTourSeen, tourSeen } from "@/lib/dashboard-tour";

const GAP = 20; // space between the highlighted link and the card
const PAD = 6; // breathing room around the highlighted link
const EDGE = 16; // keep the card this far from the window edge

type Rect = { top: number; left: number; width: number; height: number };

/** The visible sidebar link for a step. The mobile drawer renders the same links, so take the one on screen. */
function findTarget(href: string): Rect | null {
  for (const node of document.querySelectorAll<HTMLElement>(`[data-tour="${href}"]`)) {
    const r = node.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return { top: r.top, left: r.left, width: r.width, height: r.height };
  }
  return null;
}

/**
 * A short, skippable walk through the sidebar: a spotlight on one link and a card saying what the page is
 * for. Offers itself once per host, the first time they reach Overview, and can be replayed from Profile.
 * Skipping, finishing or pressing Esc all count as seen. With no visible sidebar (phones) the card is
 * simply centred.
 */
export function DashboardTour() {
  const { user } = useAuth();
  const pathname = usePathname();
  const userId = user?.id;
  const [step, setStep] = useState<number | null>(null);
  const [target, setTarget] = useState<Rect | null>(null);
  const [cardHeight, setCardHeight] = useState(0);
  const cardRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    if (userId) markTourSeen(userId);
    setStep(null);
  }, [userId]);

  // First visit: offer the tour once Overview has settled.
  useEffect(() => {
    if (!userId || pathname !== "/dashboard" || tourSeen(userId)) return;
    const timer = window.setTimeout(() => setStep(0), 700);
    return () => window.clearTimeout(timer);
  }, [userId, pathname]);

  // Replay.
  useEffect(() => {
    const start = () => setStep(0);
    window.addEventListener(TOUR_EVENT, start);
    return () => window.removeEventListener(TOUR_EVENT, start);
  }, []);

  // Follow the highlighted link (and re-measure on resize).
  useEffect(() => {
    if (step === null) return;
    const measure = () => setTarget(findTarget(TOUR_STEPS[step].href));
    const frame = window.requestAnimationFrame(measure);
    window.addEventListener("resize", measure);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
    };
  }, [step]);

  useLayoutEffect(() => {
    if (step !== null && cardRef.current) setCardHeight(cardRef.current.offsetHeight);
  }, [step, target]);

  // Keyboard: Esc skips, arrows step. Focus moves to the card so a screen reader reads it.
  useEffect(() => {
    if (step === null) return;
    cardRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") setStep((s) => (s === null ? s : Math.min(s + 1, TOUR_STEPS.length - 1)));
      else if (e.key === "ArrowLeft") setStep((s) => (s === null ? s : Math.max(s - 1, 0)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, close]);

  if (step === null) return null;
  const current = TOUR_STEPS[step];
  const last = step === TOUR_STEPS.length - 1;

  // Beside the link on a desktop sidebar; centred when there is no visible link to point at.
  const cardStyle: React.CSSProperties = target
    ? {
        left: Math.min(target.left + target.width + GAP, window.innerWidth - 352 - EDGE),
        top: Math.max(EDGE, Math.min(target.top + target.height / 2 - cardHeight / 2, window.innerHeight - cardHeight - EDGE)),
      }
    : { left: "50%", top: "50%", transform: "translate(-50%, -50%)" };

  return (
    <div className="fixed inset-0 z-[70]">
      {/* Swallows clicks and hover (so the sidebar can't pop open underneath) while the tour is up. */}
      <div className="absolute inset-0" aria-hidden />
      {target ? (
        <div
          aria-hidden
          className="pointer-events-none absolute rounded-lg border-2 border-card transition-all duration-200"
          style={{
            top: target.top - PAD,
            left: target.left - PAD,
            width: target.width + PAD * 2,
            height: target.height + PAD * 2,
            boxShadow: "0 0 0 100vmax rgba(42, 36, 32, 0.6)",
          }}
        />
      ) : (
        <div aria-hidden className="pointer-events-none absolute inset-0 bg-foreground/60" />
      )}

      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        tabIndex={-1}
        className="absolute w-[min(22rem,calc(100vw-2rem))] border border-foreground bg-card p-5 outline-none"
        style={cardStyle}
      >
        <div className="mb-4 flex gap-1" role="progressbar" aria-valuemin={1} aria-valuemax={TOUR_STEPS.length} aria-valuenow={step + 1}>
          {TOUR_STEPS.map((s, i) => (
            <div key={s.href} className={`h-1 flex-1 ${i <= step ? "bg-foreground" : "bg-border"}`} />
          ))}
        </div>
        <p className="text-xs font-bold tracking-[0.1em] text-muted-foreground uppercase">
          {step + 1} of {TOUR_STEPS.length}
        </p>
        <h2 id="tour-title" className="mt-1 font-heading text-lg font-bold text-heading">
          {current.title}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{current.body}</p>
        <div className="mt-5 flex items-center gap-2">
          <button
            type="button"
            onClick={close}
            className="mr-auto text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            Skip tour
          </button>
          {step > 0 && (
            <Button variant="outline" size="sm" onClick={() => setStep(step - 1)}>
              Back
            </Button>
          )}
          <Button variant="cta" size="sm" onClick={last ? close : () => setStep(step + 1)}>
            {last ? "Done" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  );
}
