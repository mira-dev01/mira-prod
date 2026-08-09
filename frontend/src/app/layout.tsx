import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { ClerkProvider } from "@clerk/nextjs";
import { AuthProvider } from "@/lib/auth-context";
import { Toaster } from "@/components/ui/sonner";

// Variable fonts -- no `weight` needed, the browser interpolates whatever
// Tailwind's font-medium/font-semibold/font-bold classes ask for along each
// font's own weight axis.
const boska = localFont({
  variable: "--font-boska",
  src: [
    { path: "./fonts/boska/Boska-Variable.woff2", style: "normal" },
    { path: "./fonts/boska/Boska-VariableItalic.woff2", style: "italic" },
  ],
});

const pilcrowRounded = localFont({
  variable: "--font-pilcrow-rounded",
  src: "./fonts/pilcrow-rounded/PilcrowRounded-Variable.woff2",
});

// Loaded but not wired into the active theme yet -- the alternate
// Melodrama + Nunito pairing, ready to swap to in globals.css without
// needing to touch font loading again.
const nunito = localFont({
  variable: "--font-nunito",
  src: "./fonts/nunito/Nunito-Variable.woff2",
});

const melodrama = localFont({
  variable: "--font-melodrama",
  src: "./fonts/melodrama/Melodrama-Variable.woff2",
});

// Active theme (see globals.css): Clash Display for headings, Amulya for
// body, New Title for subheadings/table headers, Alex Brush for the "mira"
// wordmark only (sidebar logo, login card, public gallery header).
const clashDisplay = localFont({
  variable: "--font-clash-display",
  src: "./fonts/clash-display/ClashDisplay-Variable.woff2",
});

const amulya = localFont({
  variable: "--font-amulya",
  src: [
    { path: "./fonts/amulya/Amulya-Variable.woff2", style: "normal" },
    { path: "./fonts/amulya/Amulya-VariableItalic.woff2", style: "italic" },
  ],
});

const newTitle = localFont({
  variable: "--font-new-title",
  src: "./fonts/new-title/NewTitle-Variable.woff2",
});

// Single static weight (script/handwriting face, no variable axis) -- used
// only for the brand wordmark itself, never for general body/heading text.
const alexBrush = localFont({
  variable: "--font-alex-brush",
  src: "./fonts/alex-brush/AlexBrush-Regular.ttf",
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
      className={`${boska.variable} ${pilcrowRounded.variable} ${nunito.variable} ${melodrama.variable} ${clashDisplay.variable} ${amulya.variable} ${newTitle.variable} ${alexBrush.variable} h-full antialiased`}
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
              fontFamily: "var(--font-amulya), sans-serif",
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
