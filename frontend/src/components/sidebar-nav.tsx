"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import {
  Home,
  Building2,
  Calendar,
  Phone,
  Users,
  UserRound,
  HelpCircle,
  Settings,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { TalkToMiraDialog } from "@/components/talk-to-mira-dialog";
import { useAuth } from "@/lib/auth-context";

// Technicians and AI Training moved into Settings as tabs (see
// app/dashboard/settings/page.tsx) rather than their own top-level nav
// entries -- both are host configuration, same category as the rest of
// what lives in Settings, not something checked day-to-day like Calls/Leads.
const links: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/dashboard", label: "Overview", icon: Home },
  { href: "/dashboard/properties", label: "Properties", icon: Building2 },
  { href: "/dashboard/calendar", label: "Calendar", icon: Calendar },
  { href: "/dashboard/calls", label: "Calls", icon: Phone },
  { href: "/dashboard/leads", label: "Leads", icon: Users },
  { href: "/dashboard/guests", label: "Guests", icon: UserRound },
  { href: "/dashboard/faq", label: "FAQ", icon: HelpCircle },
  { href: "/dashboard/settings", label: "Settings", icon: Settings },
];

function MiraLogo() {
  return (
    <span className="text-2xl text-foreground">
      {/* ︎ forces text (not emoji) rendering of ✳ on mobile -- kept in the
          default body font, not brand-logo: Alex Brush (script/handwriting)
          doesn't carry a good "✳" glyph. */}
      <span className="mr-1 text-[var(--accent-warm)]">{"✳︎"}</span>
      <span className="brand-logo">mira</span>
    </span>
  );
}

function NavLinks({ onNavigate, onTalkToMira }: { onNavigate?: () => void; onTalkToMira: () => void }) {
  const pathname = usePathname();
  const { user, logout, isInternalOrg } = useAuth();

  return (
    <>
      <nav className="flex flex-1 flex-col gap-1">
        {isInternalOrg && (
          <button
            type="button"
            onClick={() => {
              onTalkToMira();
              onNavigate?.();
            }}
            className="rounded-lg px-3 py-2 text-left text-sm font-medium text-accent-foreground transition-colors duration-150 hover:bg-accent"
          >
            <span className="mr-1.5 text-[var(--accent-warm)]">{"✳︎"}</span>
            Talk to Mira
          </button>
        )}
        <span className="text-micro px-2 pb-1 pt-2">Main</span>
        {links.map((link) => {
          const active =
            link.href === "/dashboard"
              ? pathname === link.href
              : pathname.startsWith(link.href);
          const Icon = link.icon;
          return (
            <Link
              key={link.href}
              href={link.href}
              onClick={onNavigate}
              className={cn(
                "flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
                active
                  ? "bg-accent font-medium text-accent-foreground"
                  : "font-normal text-muted-foreground"
              )}
            >
              <Icon className="size-4 shrink-0" />
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
  const [talkOpen, setTalkOpen] = useState(false);
  const pathname = usePathname();

  // Belt-and-suspenders close on route change -- NavLinks' onNavigate
  // already closes on a direct link click, but this also covers browser
  // back/forward and any programmatic router.push() elsewhere in the app.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <>
      {/* ── Desktop sidebar (md+): a permanently visible rail, not a
          dismissible overlay, so it stays plain markup rather than a Drawer
          instance (Drawer's backdrop/focus-trap/scroll-lock machinery is for
          the mobile overlay case below). sticky top-0 (on top of h-screen)
          anchors it to the viewport rather than the document flow, so it
          can never scroll out of view even if some page's content pushes
          the surrounding layout taller than the viewport. ── */}
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-card p-4 md:flex">
        <div className="mb-6 px-2">
          <MiraLogo />
          <p className="mt-0.5 text-xs text-muted-foreground">Host dashboard</p>
        </div>
        <NavLinks onTalkToMira={() => setTalkOpen(true)} />
      </aside>

      {/* ── Mobile top bar + slide-in drawer, built on @base-ui/react's
          Drawer primitive: portal, backdrop, focus trap, scroll lock, and
          swipe-to-dismiss all come from the library instead of a hand-rolled
          translate-x + manual document.body.style.overflow toggle. ── */}
      <Drawer.Root open={open} onOpenChange={setOpen} swipeDirection="left">
        <header className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between border-b bg-card px-4 h-14">
          <MiraLogo />
          <Drawer.Trigger
            aria-label="Open menu"
            className="flex h-9 w-9 items-center justify-center rounded-md text-foreground hover:bg-accent"
          >
            <Menu className="size-5" />
          </Drawer.Trigger>
        </header>

        <Drawer.Portal>
          <Drawer.Backdrop className="md:hidden fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0" />
          <Drawer.Viewport className="md:hidden fixed inset-0 z-50 flex items-stretch justify-start">
            <Drawer.Popup
              className={cn(
                "flex h-full w-64 flex-col bg-card p-4 outline-none",
                "transition-transform duration-200 [transform:translateX(var(--drawer-swipe-movement-x))]",
                "data-starting-style:-translate-x-full data-ending-style:-translate-x-full"
              )}
            >
              <div className="mb-6 flex items-center justify-between px-2">
                <div>
                  <MiraLogo />
                  <p className="mt-0.5 text-xs text-muted-foreground">Host dashboard</p>
                </div>
                <Drawer.Close
                  aria-label="Close menu"
                  className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
                >
                  <X className="size-4" />
                </Drawer.Close>
              </div>
              <NavLinks onNavigate={() => setOpen(false)} onTalkToMira={() => setTalkOpen(true)} />
            </Drawer.Popup>
          </Drawer.Viewport>
        </Drawer.Portal>
      </Drawer.Root>

      <TalkToMiraDialog open={talkOpen} onOpenChange={setTalkOpen} />
    </>
  );
}
