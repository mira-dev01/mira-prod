import type { Metadata } from "next";
import { Red_Hat_Display, Red_Hat_Text } from "next/font/google";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import { AuthProvider } from "@/lib/auth-context";
import { Toaster } from "@/components/ui/sonner";

// Active theme (see globals.css): Red Hat Text for body, UI and table
// headers; Red Hat Display for headings, the "mira" wordmark and key
// numbers. Both are variable fonts, so Tailwind's font-medium/font-semibold/
// font-bold classes interpolate along their own weight axis; no weight list
// needed.
const redHatText = Red_Hat_Text({
  variable: "--font-red-hat-text",
  subsets: ["latin"],
});
const redHatDisplay = Red_Hat_Display({
  variable: "--font-red-hat-display",
  subsets: ["latin"],
});
export const metadata: Metadata = {
  title: "MIRA — Host Dashboard",
  description: "AI voice receptionist for Airbnb hosts",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${redHatText.variable} ${redHatDisplay.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background">
        <ClerkProvider
          appearance={{
            options: { logoImageUrl: "/brand/mira-logo.svg", logoLinkUrl: "/" },
            variables: {
              // Mirrors the app's own CSS custom properties (globals.css) --
              // kept as literal hex/rgba here since Clerk's hosted
              // components render in their own isolated context, not this
              // page's DOM, so `var(--primary)` etc. wouldn't resolve.
              colorPrimary: "#b8452f",
              colorBackground: "#fbf7ef",
              colorForeground: "#2a2420",
              colorMutedForeground: "#6f6252",
              colorInput: "#f3ede2",
              colorInputForeground: "#2a2420",
              colorBorder: "#e3d9c8",
              borderRadius: "0.25rem",
              fontFamily: "var(--font-red-hat-text), sans-serif",
            },
            elements: {
              // Flat ink-ruled card, like every block in the dashboard.
              card: { boxShadow: "none", border: "1px solid #2a2420", borderRadius: "0.25rem" },
              formButtonPrimary: { boxShadow: "none" },
            },
          }}
          localization={{
            createOrganization: {
              title: "Set up your account",
              // @ts-expect-error -- subtitle exists in Clerk's actual runtime
              // localization dictionary for this screen, just not yet
              // reflected in @clerk/shared's published .d.ts types.
              subtitle: "Tell us a bit about your property business to get started.",
              formButtonSubmit: "Continue",
            },
            formFieldLabel__organizationName: "Business name",
            formFieldInputPlaceholder__organizationName: "e.g. Pause Projects Goa",
          }}
        >
          <AuthProvider>{children}</AuthProvider>
        </ClerkProvider>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
