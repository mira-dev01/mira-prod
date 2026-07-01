"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";

const links = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/properties", label: "Properties" },
  { href: "/dashboard/calendar", label: "Calendar" },
  { href: "/dashboard/calls", label: "Calls" },
  { href: "/dashboard/leads", label: "Leads" },
  { href: "/dashboard/guests", label: "Guests" },
  { href: "/dashboard/pricing", label: "Pricing" },
  { href: "/dashboard/faq", label: "FAQ" },
  { href: "/dashboard/technicians", label: "Technicians" },
  { href: "/dashboard/settings", label: "Settings" },
];

function MiraLogo() {
  return (
    <span className="font-display text-2xl italic text-foreground">
      {/* ︎ forces text (not emoji) rendering of ✳ on mobile */}
      <span className="mr-1 text-[var(--accent-warm)]">{"✳︎"}</span>
      Mira
    </span>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <>
      <nav className="flex flex-1 flex-col gap-1">
        <span className="text-micro px-2 pb-1 pt-2">Main</span>
        {links.map((link) => {
          const active =
            link.href === "/dashboard"
              ? pathname === link.href
              : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              onClick={onNavigate}
              className={cn(
                "rounded-[var(--radius)] px-3 py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
                active
                  ? "bg-accent font-medium text-accent-foreground"
                  : "font-normal text-muted-foreground"
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
      <div className="space-y-2 border-t pt-4">
        <p className="truncate px-2 text-xs text-muted-foreground">{user?.email}</p>
        <Button variant="outline" size="sm" className="w-full" onClick={logout}>
          Log out
        </Button>
      </div>
    </>
  );
}

export function SidebarNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Close drawer on route change
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  return (
    <>
      {/* ── Desktop sidebar (md+) ── */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col border-r bg-card p-4">
        <div className="mb-6 px-2">
          <MiraLogo />
          <p className="mt-0.5 text-xs text-muted-foreground">Host dashboard</p>
        </div>
        <NavLinks />
      </aside>

      {/* ── Mobile top bar ── */}
      <header className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between border-b bg-card px-4 h-14">
        <MiraLogo />
        <button
          aria-label="Open menu"
          onClick={() => setOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-foreground hover:bg-accent"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <rect y="3" width="20" height="2" rx="1" fill="currentColor" />
            <rect y="9" width="20" height="2" rx="1" fill="currentColor" />
            <rect y="15" width="20" height="2" rx="1" fill="currentColor" />
          </svg>
        </button>
      </header>

      {/* ── Mobile drawer backdrop ── */}
      {open && (
        <div
          className="md:hidden fixed inset-0 z-50 bg-black/40"
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Mobile drawer panel ── */}
      <aside
        className={cn(
          "md:hidden fixed top-0 left-0 z-50 h-full w-64 bg-card flex flex-col p-4 transition-transform duration-200",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="mb-6 flex items-center justify-between px-2">
          <div>
            <MiraLogo />
            <p className="mt-0.5 text-xs text-muted-foreground">Host dashboard</p>
          </div>
          <button
            aria-label="Close menu"
            onClick={() => setOpen(false)}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M2 2l12 12M14 2L2 14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <NavLinks onNavigate={() => setOpen(false)} />
      </aside>
    </>
  );
}
