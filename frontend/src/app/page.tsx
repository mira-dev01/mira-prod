"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { LandingHero } from "@/components/hero/landing-hero";

export default function RootIndex() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // "/" is the logged-out marketing page only -- an already-authenticated
  // visitor (post-login redirect landed here from a stale link, browser
  // back/forward, or the logo) should never see it again, mirroring
  // dashboard/layout.tsx's redirect in the opposite direction.
  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [loading, user, router]);

  if (loading || user) return null;

  return <LandingHero user={user} />;
}
