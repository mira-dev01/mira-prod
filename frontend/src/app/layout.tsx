import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
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
      className={`${boska.variable} ${pilcrowRounded.variable} ${nunito.variable} ${melodrama.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background">
        <AuthProvider>{children}</AuthProvider>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
