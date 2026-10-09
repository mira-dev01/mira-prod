"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import { ChevronDown, Menu, Pencil, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { NavigationEditor } from "@/components/navigation-editor";
import { TalkToMiraDialog } from "@/components/talk-to-mira-dialog";
import { useAuth } from "@/lib/auth-context";
import { FALLBACK_NAV, activeNavId, NavIcon } from "@/lib/navigation";
import { useNavigation } from "@/lib/navigation-context";
import type { NavItem } from "@/lib/types";

// Destinations, their order and which ones the host has hidden come from
// the backend (GET /preferences/navigation -- app/services/
// navigation_registry.py), already reconciled against the host's
// capabilities; lib/navigation.ts only adds icons and the fallback list.
// Technicians stays a Settings tab and AI Training a top-level entry, as
// before. Hiding an entry is layout only -- never a capability change and
// never an access control (every page's data is still guarded server-side).

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

function NavLink({ item, active, muted, onNavigate }: { item: NavItem; active: boolean; muted?: boolean; onNavigate?: () => void }) {
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
        active ? "bg-accent font-medium text-accent-foreground" : "font-normal text-muted-foreground",
        muted && !active && "opacity-70"
      )}
    >
      <NavIcon id={item.id} className="size-4 shrink-0" />
      {item.label}
    </Link>
  );
}

function NavLinks({ onNavigate, onTalkToMira }: { onNavigate?: () => void; onTalkToMira: () => void }) {
  const pathname = usePathname();
  const { user, logout, isInternalOrg } = useAuth();
  const { prefs, loading, error, editing, setEditing } = useNavigation();
  const [showHidden, setShowHidden] = useState(false);

  // While loading, show placeholders rather than a guessed menu; if the
  // preferences can't load at all, fall back to the default destinations
  // so a network error never strands the host without navigation.
  const items: NavItem[] | null = prefs?.items ?? (error && !loading ? FALLBACK_NAV : null);
  const visible = (items ?? []).filter((i) => i.available && !i.hidden);
  const hidden = (items ?? []).filter((i) => i.available && i.hidden);
  const activeId = activeNavId([...visible, ...hidden], pathname);

  return (
    <>
      {editing && prefs ? (
        <NavigationEditor key={prefs.revision} prefs={prefs} onClose={() => setEditing(false)} />
      ) : (
        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto" aria-label="Main">
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
          <div className="flex items-center justify-between px-2 pb-1 pt-2">
            <span className="text-micro">Main</span>
            {prefs && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                aria-label="Customize navigation"
              >
                <Pencil className="size-3" />
                Edit
              </button>
            )}
          </div>
          {items === null
            ? Array.from({ length: 8 }, (_, i) => <Skeleton key={i} variant="text" className="mx-3 my-2 h-4" />)
            : visible.map((item) => (
                <NavLink key={item.id} item={item} active={item.id === activeId} onNavigate={onNavigate} />
              ))}
          {hidden.length > 0 && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setShowHidden((v) => !v)}
                aria-expanded={showHidden}
                className="flex w-full items-center gap-1 px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ChevronDown className={cn("size-3 transition-transform", !showHidden && "-rotate-90")} />
                Hidden ({hidden.length})
              </button>
              {showHidden &&
                hidden.map((item) => (
                  <NavLink key={item.id} item={item} active={item.id === activeId} muted onNavigate={onNavigate} />
                ))}
            </div>
          )}
        </nav>
      )}
      <div className="space-y-2 border-t pt-4">
        <Link
          href="/dashboard/profile"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-accent"
        >
          <Avatar size="sm">
            <AvatarImage src={user?.photo_url ?? undefined} alt="" />
            <AvatarFallback>{(user?.name ?? user?.email ?? "?").charAt(0).toUpperCase()}</AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1 truncate text-xs font-medium">
            {user?.name || user?.business_name || user?.email}
          </span>
        </Link>
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
  const { editing } = useNavigation();

  // Belt-and-suspenders close on route change -- NavLinks' onNavigate
  // already closes on a direct link click, but this also covers browser
  // back/forward and any programmatic router.push() elsewhere in the app.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // "Customize navigation" can be started from Settings -- on mobile the
  // editor lives in the drawer, so open it. Mobile only: the drawer is
  // modal (focus trap, inert background), so opening it while it's
  // display:none on desktop would trap focus in an invisible panel.
  useEffect(() => {
    if (!editing || !window.matchMedia("(max-width: 767px)").matches) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing the drawer to an external edit request
    setOpen(true);
  }, [editing]);

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
