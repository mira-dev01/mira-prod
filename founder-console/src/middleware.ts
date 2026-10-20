import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "founder_session";

// Not full auth -- a single shared passphrase (see .env.example) gating
// access to internal API-cost/health data that must never surface in the
// host-facing dashboard. Good enough for "founders only", not for anything
// handling real credentials.
export function middleware(request: NextRequest) {
  const isPublic =
    request.nextUrl.pathname.startsWith("/login") ||
    request.nextUrl.pathname.startsWith("/api/login");
  if (isPublic) return NextResponse.next();

  const cookie = request.cookies.get(SESSION_COOKIE);
  if (cookie?.value === "ok") return NextResponse.next();

  const loginUrl = new URL("/login", request.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
