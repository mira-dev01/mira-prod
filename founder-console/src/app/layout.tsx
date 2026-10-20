import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Mira — Founder Console",
  description: "Internal API health & cost console. Not linked from the host dashboard.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          background: "#f5f0e8",
          color: "#1a1714",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif",
        }}
      >
        {children}
      </body>
    </html>
  );
}
