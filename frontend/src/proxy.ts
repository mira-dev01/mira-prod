import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// /p/[propertyId] is the public guest-facing property gallery -- never
// gated. /login and /sign-up are Clerk's own catch-all auth routes.
<<<<<<< HEAD
// Everything else under /dashboard requires a signed-in session.
const isPublicRoute = createRouteMatcher(["/", "/login(.*)", "/sign-up(.*)", "/p(.*)"]);
=======
// /admin is the internal operator panel: NOT gated by Clerk because it has
// its own email one-time-code session (backend app/auth/admin.py) -- every
// /api/v1/admin/* call is rejected server-side without that token, and
// app/admin/layout.tsx redirects to /admin/login when there isn't one.
// Everything else under /dashboard requires a signed-in session.
const isPublicRoute = createRouteMatcher(["/", "/login(.*)", "/sign-up(.*)", "/p(.*)", "/admin(.*)"]);
>>>>>>> 368377a (20th October)

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)"],
};
