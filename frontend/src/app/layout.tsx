import type { Metadata } from "next";
import { Libre_Baskerville, Montserrat } from "next/font/google";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import { AuthProvider } from "@/lib/auth-context";
import { Toaster } from "@/components/ui/sonner";

// Active theme (see globals.css): Libre Baskerville for headings/table
// subheadings + the "mira" wordmark, Montserrat for body/UI text.
// Libre Baskerville has no variable axis (fixed 400/700 cuts, each with a
// true italic) -- weight/style declared explicitly. Montserrat is a
// variable font, so no weight needed; Tailwind's font-medium/font-semibold/
// font-bold classes interpolate along its own weight axis.
const libreBaskerville = Libre_Baskerville({
  variable: "--font-libre-baskerville",
  subsets: ["latin"],
  weight: ["400", "700"],
  style: ["normal", "italic"],
});

const montserrat = Montserrat({
  variable: "--font-montserrat",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "MIRA — Host Dashboard",
  description: "AI voice receptionist for Airbnb hosts",
  icons: {
    // SVG favicon — forces text (not emoji) rendering in all browsers
    icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90' font-family='serif' font-style='italic'>M</text></svg>",
    apple: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90' font-family='serif' font-style='italic'>M</text></svg>",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${libreBaskerville.variable} ${montserrat.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background">
        <ClerkProvider
          appearance={{
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
              borderRadius: "0.625rem",
              fontFamily: "var(--font-montserrat), sans-serif",
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
