"use client";

import { MiraMark, MiraWordmark } from "@/components/brand/mira-logo";
import * as React from "react";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// Collapsed-by-default desktop sidebar shared by the host dashboard
// (components/sidebar-nav.tsx) and the admin console
// (components/admin/admin-sidebar.tsx).
//
// Structure: an in-flow, fixed-width (w-16) placeholder is all the page
// layout ever sees, so <main> always lays out against the 64px rail. The real
// <aside> is absolutely positioned inside it and widens on hover/focus --
// OVERLAYING <main> rather than pushing it, so expanding never reflows the
// page (no content jump, and only this element's own width is animated).
//
// Icon x-positions are identical collapsed and expanded: the aside has px-2
// and every row adds its own padding so the icon's centre sits at 32px (the
// middle of the 64px rail); expanding just reveals labels to the icon's right.

type RailState = { rail: boolean; expanded: boolean };

// Default = "not in a rail" (the mobile drawer renders the same nav
// components outside any RailSidebar): always-expanded, original paddings.
const RailContext = React.createContext<RailState>({ rail: false, expanded: true });

export function useRail() {
  return React.useContext(RailContext);
}

const OPEN_DELAY_MS = 80;
const CLOSE_DELAY_MS = 150;

export function RailSidebar({
  expandedWidthClass = "w-56",
  children,
}: {
  // Full Tailwind class (must be written out statically so it's generated).
  expandedWidthClass?: string;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = React.useState(false);
  const rootRef = React.useRef<HTMLElement>(null);
  const timer = React.useRef<number | undefined>(undefined);
  const hovering = React.useRef(false);
  const pointerType = React.useRef<string>("mouse");
  const pathname = usePathname();

  const schedule = React.useCallback((open: boolean, delay: number) => {
    window.clearTimeout(timer.current);
    if (delay === 0) {
      setExpanded(open);
      return;
    }
    timer.current = window.setTimeout(() => setExpanded(open), delay);
  }, []);

  React.useEffect(() => () => window.clearTimeout(timer.current), []);

  // A mouse/keyboard user stays expanded after clicking a link until the
  // pointer leaves (so it doesn't snap shut under the cursor); a touch user
  // has no "leave", so collapse once the navigation lands.
  React.useEffect(() => {
    if (pointerType.current === "touch") setExpanded(false);
  }, [pathname]);

  // Tap-outside (touch) and Escape (keyboard) both collapse.
  React.useEffect(() => {
    if (!expanded) return;
    function onPointerDown(e: PointerEvent) {
      if (!rootRef.current?.contains(e.target as Node)) {
        hovering.current = false;
        setExpanded(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setExpanded(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [expanded]);

  function focusIsVisibleInside() {
    const active = document.activeElement;
    return !!active && !!rootRef.current?.contains(active) && active.matches(":focus-visible");
  }

  const value = React.useMemo(() => ({ rail: true, expanded }), [expanded]);

  return (
    <div className="relative hidden w-16 shrink-0 md:block">
      <RailContext.Provider value={value}>
        <aside
          ref={rootRef}
          data-expanded={expanded}
          onPointerDownCapture={(e) => {
            pointerType.current = e.pointerType;
          }}
          onPointerEnter={(e) => {
            pointerType.current = e.pointerType;
            if (e.pointerType === "touch") return; // touch has no hover -- see onClickCapture
            hovering.current = true;
            schedule(true, OPEN_DELAY_MS);
          }}
          onPointerLeave={(e) => {
            if (e.pointerType === "touch") return;
            hovering.current = false;
            if (!focusIsVisibleInside()) schedule(false, CLOSE_DELAY_MS);
          }}
          // First tap on a collapsed rail (touch devices at md+, e.g. a tablet)
          // expands it instead of navigating; the next tap navigates.
          onClickCapture={(e) => {
            if (pointerType.current === "touch" && !expanded) {
              e.preventDefault();
              e.stopPropagation();
              setExpanded(true);
            }
          }}
          // Keyboard users: tabbing in expands (only for :focus-visible, so a
          // mouse click's focus doesn't pin it open after the pointer leaves).
          onFocus={(e) => {
            if (e.target.matches(":focus-visible")) schedule(true, 0);
          }}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null) && !hovering.current) {
              schedule(false, CLOSE_DELAY_MS);
            }
          }}
          className={cn(
            "absolute inset-y-0 left-0 z-30 flex flex-col overflow-hidden border-r bg-card px-2 py-4",
            "transition-[width,box-shadow] duration-200 ease-out motion-reduce:transition-none",
            expanded ? cn(expandedWidthClass, "shadow-xl") : "w-16"
          )}
        >
          {children}
        </aside>
      </RailContext.Provider>
    </div>
  );
}

// Text that fades in with the rail. Outside a rail (mobile drawer) it's a
// plain span, so the drawer renders exactly as before. Stays in the DOM when
// collapsed (opacity only), so screen readers still get every label.
export function RailLabel({
  className,
  children,
  ...props
}: React.ComponentProps<"span">) {
  const { rail, expanded } = useRail();
  return (
    <span
      className={cn(
        rail && "whitespace-nowrap transition-opacity duration-150 motion-reduce:transition-none",
        rail && !expanded && "opacity-0",
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

// Nav group header: the usual micro label when expanded; collapsed to a thin
// divider so grouping stays visible in the icon-only rail. Fixed height in
// both states so rows never shift vertically during the animation.
export function RailSection({
  children,
  heightClass = "h-7",
  topSpaceClass = "pt-2",
}: {
  children: React.ReactNode;
  heightClass?: string;
  // Only used outside a rail (drawer), to keep its original spacing.
  topSpaceClass?: string;
}) {
  const { rail, expanded } = useRail();
  if (!rail) return <span className={cn("text-micro px-2 pb-1", topSpaceClass)}>{children}</span>;
  return (
    <div className={cn("relative shrink-0", heightClass)}>
      <span
        className={cn(
          "text-micro absolute inset-x-2 bottom-1 whitespace-nowrap transition-opacity duration-150 motion-reduce:transition-none",
          expanded ? "opacity-100" : "opacity-0"
        )}
      >
        {children}
      </span>
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-4 bottom-2.5 border-t transition-opacity duration-150 motion-reduce:transition-none",
          expanded ? "opacity-0" : "opacity-100"
        )}
      />
    </div>
  );
}

// Logo block for the rail: the arch mark stays put (centred in the 64px rail);
// the wordmark and subtitle fade in beside/below it.
export function RailBrand({ subtitle }: { subtitle: string }) {
  return (
    <div className="mb-6 shrink-0 px-2">
      <div className="flex items-center text-foreground">
        <span className="flex w-8 shrink-0 justify-center">
          <MiraMark className="h-7" />
        </span>
        <RailLabel className="ml-2">
          <MiraWordmark className="h-6" />
        </RailLabel>
      </div>
      <RailLabel className="mt-0.5 block text-xs text-muted-foreground">{subtitle}</RailLabel>
    </div>
  );
}
