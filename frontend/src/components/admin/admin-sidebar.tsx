"use client";

import { MiraLogo } from "@/components/brand/mira-logo";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import {
  AudioLines,
  Flag,
  Gauge,
  LayoutDashboard,
  Menu,
  MessagesSquare,
  Users,
  Building,
  Receipt,
  UserRound,
  Wallet,
  LogOut,
  X,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAdmin } from "@/components/admin/admin-context";
import { RailBrand, RailLabel, RailSection, RailSidebar, useRail } from "@/components/rail-sidebar";

// Same rail/drawer structure as components/sidebar-nav.tsx (the host
// dashboard) -- an icon-only rail that widens on hover (components/
// rail-sidebar.tsx) -- grouped into the admin panel's three areas.
const groups: { title: string; links: { href: string; label: string; icon: LucideIcon }[] }[] = [
  { title: "Monitor", links: [{ href: "/admin", label: "Overview", icon: LayoutDashboard }] },
  {
    title: "Agent quality",
    links: [
      { href: "/admin/audio", label: "Voice & audio", icon: AudioLines },
      { href: "/admin/flagged", label: "Flagged calls", icon: Flag },
      { href: "/admin/conversations", label: "Conversations", icon: MessagesSquare },
      { href: "/admin/performance", label: "Speed & reliability", icon: Gauge },
      { href: "/admin/leads", label: "Leads & escalations", icon: Users },
      { href: "/admin/hosts", label: "Hosts & properties", icon: Building },
    ],
  },
  {
    title: "Billing",
    links: [
      { href: "/admin/usage", label: "Usage & cost", icon: Receipt },
      { href: "/admin/balances", label: "Balances", icon: Wallet },
    ],
  },
];

function Logo() {
  return (
    <MiraLogo className="h-7 text-foreground" />
  );
}

function Links({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { email, logout } = useAdmin();
  // Rail vs. mobile drawer paddings -- see components/rail-sidebar.tsx.
  const { rail } = useRail();
  return (
    <>
      <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden">
        {groups.map((group) => (
          <div key={group.title} className="flex flex-col gap-1">
            <RailSection heightClass="h-8" topSpaceClass="pt-3">
              {group.title}
            </RailSection>
            {group.links.map((link) => {
              const active = link.href === "/admin" ? pathname === "/admin" : pathname.startsWith(link.href);
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-2 overflow-hidden py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
                    rail ? "px-4" : "px-3",
                    active ? "bg-accent font-medium text-accent-foreground" : "font-normal text-muted-foreground"
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  <RailLabel>{link.label}</RailLabel>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="shrink-0 space-y-2 border-t pt-4">
        {rail ? (
          <div className="flex items-center gap-2 overflow-hidden px-4 py-1.5" title={email}>
            <UserRound className="size-4 shrink-0 text-muted-foreground" />
            <RailLabel className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{email}</RailLabel>
          </div>
        ) : (
          <p className="truncate px-2 text-xs text-muted-foreground">{email}</p>
        )}
        <Button
          variant="outline"
          size="sm"
          className={cn("w-full overflow-hidden", rail && "justify-start gap-2 px-[15px]")}
          onClick={logout}
        >
          {rail && <LogOut className="size-4 shrink-0" />}
          <RailLabel>Log out</RailLabel>
        </Button>
      </div>
    </>
  );
}

export function AdminSidebar() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <>
      <RailSidebar expandedWidthClass="w-60">
        <RailBrand subtitle="Admin console" />
        <Links />
      </RailSidebar>

      <Drawer.Root open={open} onOpenChange={setOpen} swipeDirection="left">
        <header className="fixed left-0 right-0 top-0 z-40 flex h-14 items-center justify-between border-b bg-card px-4 md:hidden">
          <Logo />
          <Drawer.Trigger
            aria-label="Open menu"
            className="flex h-9 w-9 items-center justify-center rounded-md text-foreground hover:bg-accent"
          >
            <Menu className="size-5" />
          </Drawer.Trigger>
        </header>
        <Drawer.Portal>
          <Drawer.Backdrop className="fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0 md:hidden" />
          <Drawer.Viewport className="fixed inset-0 z-50 flex items-stretch justify-start md:hidden">
            <Drawer.Popup
              className={cn(
                "flex h-full w-64 flex-col bg-card p-4 outline-none",
                "transition-transform duration-200 [transform:translateX(var(--drawer-swipe-movement-x))]",
                "data-starting-style:-translate-x-full data-ending-style:-translate-x-full"
              )}
            >
              <div className="mb-4 flex items-center justify-between px-2">
                <div>
                  <Logo />
                  <p className="mt-0.5 text-xs text-muted-foreground">Admin console</p>
                </div>
                <Drawer.Close
                  aria-label="Close menu"
                  className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
                >
                  <X className="size-4" />
                </Drawer.Close>
              </div>
              <Links onNavigate={() => setOpen(false)} />
            </Drawer.Popup>
          </Drawer.Viewport>
        </Drawer.Portal>
      </Drawer.Root>
    </>
  );
}
