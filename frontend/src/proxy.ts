import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// /p/[propertyId] is the public guest-facing property gallery -- never
// gated. /login and /sign-up are Clerk's own catch-all auth routes.
// Everything else under /dashboard requires a signed-in session.
const isPublicRoute = createRouteMatcher(["/", "/login(.*)", "/sign-up(.*)", "/p(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)"],
};
