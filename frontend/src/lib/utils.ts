import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Matches app/services/call_service.py's BROWSER_TEST_CALLER_NUMBER -- the
// placeholder caller identity used by the dashboard's "test in browser"
// feature, since there's no real phone number for a WebRTC test call.
export const BROWSER_TEST_CALLER_NUMBER = "browser-test"

export function isBrowserTestIdentity(value: string | null | undefined): boolean {
  return value === BROWSER_TEST_CALLER_NUMBER
}
