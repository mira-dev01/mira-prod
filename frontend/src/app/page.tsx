"use client";

import { useAuth } from "@/lib/auth-context";
import { LandingHero } from "@/components/hero/landing-hero";

export default function RootIndex() {
  const { user, loading } = useAuth();

  if (loading) return null;

  return <LandingHero user={user} />;
}
