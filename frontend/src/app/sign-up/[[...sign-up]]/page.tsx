"use client";

import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <SignUp path="/sign-up" routing="path" signInUrl="/login" fallbackRedirectUrl="/dashboard/onboarding" />
    </div>
  );
}
