"use client";

import { MiraLogo } from "@/components/brand/mira-logo";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import {
  Home,
  AudioLines,
  Building2,
  BrainCircuit,
  Calendar,
  ChartColumn,
  IndianRupee,
  Phone,
  Users,
  UserRound,
  HelpCircle,
  Settings,
  LogOut,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { RailBrand, RailLabel, RailSection, RailSidebar, useRail } from "@/components/rail-sidebar";
import { ActivityBadge } from "@/components/activity-badge";
import { TalkToMiraDialog } from "@/components/talk-to-mira-dialog";
import { useAuth } from "@/lib/auth-context";
import { useBookingActivity } from "@/lib/booking-activity-context";
import { hrefsForInterests } from "@/lib/use-cases";

// Technicians stayed a Settings tab (see app/dashboard/settings/page.tsx) --
// host configuration, same category as the rest of Settings, not something
// checked day-to-day like Calls/Leads. AI Training (formerly the "Voice AI"
// + "AI Training" Settings tabs, merged) instead gets its own top-level nav
// entry right after Properties, since it configures the voice agent that
// serves those properties. Pricing (added alongside dashboard
// personalization, 2026-10-26) previously had NO sidebar entry at all --
// the page existed and worked, it just wasn't reachable except by typing
// the URL -- found while mapping use-case interests to nav items.
const links: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/dashboard", label: "Overview", icon: Home },
  { href: "/dashboard/analytics", label: "Analytics", icon: ChartColumn },
  { href: "/dashboard/properties", label: "Properties", icon: Building2 },
  { href: "/dashboard/agent", label: "Agent", icon: BrainCircuit },
  { href: "/dashboard/pricing", label: "Pricing", icon: IndianRupee },
  { href: "/dashboard/calendar", label: "Calendar", icon: Calendar },
  { href: "/dashboard/calls", label: "Calls", icon: Phone },
  { href: "/dashboard/leads", label: "Leads", icon: Users },
  { href: "/dashboard/guests", label: "Guests", icon: UserRound },
  { href: "/dashboard/faq", label: "FAQ", icon: HelpCircle },
  { href: "/dashboard/settings", label: "Settings", icon: Settings },
];

function NavLinks({ onNavigate, onTalkToMira }: { onNavigate?: () => void; onTalkToMira: () => void }) {
  const pathname = usePathname();
  const { user, logout, isInternalOrg } = useAuth();
  // In the desktop rail every row is padded so its icon's centre lands on the
  // rail's centre line (see rail-sidebar.tsx); the mobile drawer keeps its
  // original paddings and always-visible labels.
  const { rail } = useRail();
  // Requests from guests with an active booking: a number on the Leads link, in the rail and in the drawer.
  const { count: bookingActivity } = useBookingActivity();

  function renderLink(link: (typeof links)[number]) {
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
          "flex items-center gap-2 overflow-hidden rounded-lg py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
          rail ? "px-4" : "px-3",
          active ? "bg-accent font-medium text-accent-foreground" : "font-normal text-muted-foreground"
        )}
      >
        <span className="relative shrink-0">
          <Icon className="size-4" />
          {link.href === "/dashboard/leads" && bookingActivity > 0 && (
            <ActivityBadge count={bookingActivity} className="absolute -top-2 -right-3 size-4 text-[10px]" />
          )}
        </span>
        <RailLabel>{link.label}</RailLabel>
      </Link>
    );
  }

  // Dashboard personalization (2026-10-26): a host's onboarding-Step-0 (or
  // Profile-edited) interests promote matching nav items into a "Your
  // focus" group above everything else, which stays fully intact below as
  // "Workspace" -- nothing is removed or hidden, items only move. A host
  // with no interests recorded (hasn't opted in yet, or signed up before
  // this existed) falls through to the exact original flat "Main" list --
  // pixel-identical to pre-personalization behavior, not just
  // conceptually equivalent, so this is a strictly additive change.
  const interests = user?.interests ?? [];
  const promotedHrefs = hrefsForInterests(interests);
  const overviewLink = links.find((link) => link.href === "/dashboard")!;
  const restLinks = links.filter((link) => link.href !== "/dashboard");
  const focusLinks = promotedHrefs
    .map((href) => restLinks.find((link) => link.href === href))
    .filter((link): link is (typeof links)[number] => link !== undefined);
  const workspaceLinks = restLinks.filter((link) => !promotedHrefs.includes(link.href));

  return (
    <>
      <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden">
        {isInternalOrg && (
          <button
            type="button"
            onClick={() => {
              onTalkToMira();
              onNavigate?.();
            }}
            className={cn(
              "flex items-center gap-2 overflow-hidden rounded-lg py-2 text-left text-sm font-medium text-accent-foreground transition-colors duration-150 hover:bg-accent",
              rail ? "px-4" : "px-3"
            )}
          >
            <AudioLines className="size-4 shrink-0 text-(--accent-warm)" />
            <RailLabel>Talk to Mira</RailLabel>
          </button>
        )}
        {focusLinks.length === 0 ? (
          <>
            <RailSection>Main</RailSection>
            {links.map(renderLink)}
          </>
        ) : (
          <>
            {renderLink(overviewLink)}
            <RailSection>Your focus</RailSection>
            {focusLinks.map(renderLink)}
            <RailSection>Workspace</RailSection>
            {workspaceLinks.map(renderLink)}
          </>
        )}
      </nav>
      <div className="shrink-0 space-y-2 border-t pt-4">
        <Link
          href="/dashboard/profile"
          onClick={onNavigate}
          className={cn(
            "flex items-center gap-2 overflow-hidden rounded-lg py-1.5 transition-colors duration-150 hover:bg-accent",
            rail ? "px-3" : "px-2"
          )}
        >
          <Avatar size="sm">
            <AvatarImage src={user?.photo_url ?? undefined} alt="" />
            <AvatarFallback>{(user?.name ?? user?.email ?? "?").charAt(0).toUpperCase()}</AvatarFallback>
          </Avatar>
          <RailLabel className="min-w-0 flex-1 truncate text-xs font-medium">
            {user?.name || user?.business_name || user?.email}
          </RailLabel>
        </Link>
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
      {/* ── Desktop sidebar (md+): an icon-only rail that widens on hover /
          keyboard focus, overlaying <main> rather than pushing it (see
          components/rail-sidebar.tsx). The page layout only ever sees the
          rail's fixed 64px, so <main> gets the width a permanently expanded
          sidebar used to take. Stays plain markup rather than a Drawer
          instance (Drawer's backdrop/focus-trap/scroll-lock machinery is for
          the mobile overlay case below). It fills the height of the
          `fixed inset-0` layout row (dashboard/layout.tsx), so it can never
          scroll out of view. ── */}
      <RailSidebar expandedWidthClass="w-56">
        <RailBrand subtitle="Host dashboard" />
        <NavLinks onTalkToMira={() => setTalkOpen(true)} />
      </RailSidebar>

      {/* ── Mobile top bar + slide-in drawer, built on @base-ui/react's
          Drawer primitive: portal, backdrop, focus trap, scroll lock, and
          swipe-to-dismiss all come from the library instead of a hand-rolled
          translate-x + manual document.body.style.overflow toggle. ── */}
      <Drawer.Root open={open} onOpenChange={setOpen} swipeDirection="left">
        <header className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between border-b bg-card px-4 h-14">
          <MiraLogo className="h-7 text-foreground" />
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
                  <MiraLogo className="h-7 text-foreground" />
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
