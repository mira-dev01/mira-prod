"use client";

import { SignIn } from "@clerk/nextjs";

export default function LoginPage() {
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <SignIn path="/login" routing="path" signUpUrl="/sign-up" fallbackRedirectUrl="/dashboard" />
    </div>
  );
}
