"use client";

import { Button } from "@/components/ui/button";
import { Cell, Sheet } from "@/components/ui/sheet";
import { MiraLogo } from "@/components/brand/mira-logo";
import type { ProfileProblem } from "@/lib/auth-context";

const TITLES: Record<ProfileProblem["kind"], string> = {
  unavailable: "Can't reach your account",
  unauthorized: "Couldn't verify your sign-in",
  conflict: "This email is already in use",
  forbidden: "Account unavailable",
};

/**
 * Shown instead of the dashboard when someone is signed in but their profile can't be loaded. Never a redirect
 * to sign-in: Clerk would send a signed-in person straight back, and the cause (an outage, a suspended
 * account, an email that belongs to another sign-in) would stay unexplained. A hiccup gets "Try again"; an
 * account problem gets only "Sign out", because trying again can't change it.
 */
export function AccountProblemScreen({
  problem,
  onRetry,
  onSignOut,
}: {
  problem: ProfileProblem | null;
  onRetry: () => void;
  onSignOut: () => void;
}) {
  const kind = problem?.kind ?? "unavailable";
  const message = problem?.message ?? "We couldn't load your account. Check your connection and try again.";
  const retryable = kind === "unavailable" || kind === "unauthorized";
  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-y-auto p-4">
      <Sheet className="w-full max-w-md" role="alert">
        <Cell className="gap-4 p-6">
          <MiraLogo className="h-6 text-foreground" />
          <h1 className="font-heading text-xl font-bold">{TITLES[kind]}</h1>
          <p className="text-sm text-muted-foreground">{message}</p>
          <div className="flex gap-2 pt-1">
            {retryable && (
              <Button variant="cta" onClick={onRetry}>
                Try again
              </Button>
            )}
            <Button variant={retryable ? "outline" : "default"} onClick={onSignOut}>
              Sign out
            </Button>
          </div>
        </Cell>
      </Sheet>
    </div>
  );
}
