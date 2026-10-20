"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import {
  AudioLines,
  Gauge,
  LayoutDashboard,
  Menu,
  MessagesSquare,
  Users,
  Building,
  Receipt,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAdmin } from "@/components/admin/admin-context";

// Same rail/drawer structure as components/sidebar-nav.tsx (the host
// dashboard), grouped into the admin panel's three areas.
const groups: { title: string; links: { href: string; label: string; icon: LucideIcon }[] }[] = [
  { title: "Monitor", links: [{ href: "/admin", label: "Overview", icon: LayoutDashboard }] },
  {
    title: "Agent quality",
    links: [
      { href: "/admin/audio", label: "Voice & audio", icon: AudioLines },
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
    <span className="text-2xl text-foreground">
      <span className="mr-1 text-[var(--accent-warm)]">{"✳︎"}</span>
      <span className="brand-logo">mira</span>
    </span>
  );
}

function Links({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { email, logout } = useAdmin();
  return (
    <>
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto">
        {groups.map((group) => (
          <div key={group.title} className="flex flex-col gap-1">
            <span className="text-micro px-2 pb-1 pt-3">{group.title}</span>
            {group.links.map((link) => {
              const active = link.href === "/admin" ? pathname === "/admin" : pathname.startsWith(link.href);
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
                    active ? "bg-accent font-medium text-accent-foreground" : "font-normal text-muted-foreground"
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {link.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="space-y-2 border-t pt-4">
        <p className="truncate px-2 text-xs text-muted-foreground">{email}</p>
        <Button variant="outline" size="sm" className="w-full" onClick={logout}>
          Log out
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
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card p-4 md:flex">
        <div className="mb-4 px-2">
          <Logo />
          <p className="mt-0.5 text-xs text-muted-foreground">Admin console</p>
        </div>
        <Links />
      </aside>

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
