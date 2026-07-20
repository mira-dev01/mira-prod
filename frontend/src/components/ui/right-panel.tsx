"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { Drawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Shared right-hand slide-over panel -- extracted from the guest profile
// page's original GuestDrawer (base-ui Drawer, swipeDirection="right").
// This is the "Notion-style" side panel every dialog in the app now opens
// in, replacing the centered `Dialog` popup. Pure layout/chrome; content is
// always the caller's children.
const sizeClassName = {
  // Forms / record edits (Block dates, Edit lead, Answer FAQ gap, ...).
  md: "max-w-md",
  // Denser forms with more fields (Edit property).
  lg: "max-w-xl",
  // Media / interactive flows that need real width (Talk to Mira, image
  // lightbox) -- these aren't "a form," so they get the most room.
  xl: "max-w-3xl",
} as const;

export type RightPanelSize = keyof typeof sizeClassName;

function RightPanel({
  open,
  onOpenChange,
  title,
  size = "md",
  children,
  footer,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  size?: RightPanelSize;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  // Close automatically on route change (e.g. switching sidebar tabs from
  // Leads to Calls) rather than leaving a stale panel floating over the new
  // page -- every consumer gets this for free instead of reimplementing it.
  // Skips the mount-time run (a panel opened via a URL-driven `open` prop on
  // first render shouldn't immediately close itself).
  const pathname = usePathname();
  const isFirstRender = React.useRef(true);
  React.useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    onOpenChange(false);
    // Only route changes should trigger this -- onOpenChange is a setState
    // setter from the caller and intentionally excluded to avoid re-running
    // this effect every time the caller's own state updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange} swipeDirection="right">
      <Drawer.Portal>
        <Drawer.Backdrop className="fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Drawer.Viewport className="fixed inset-0 z-50 flex items-stretch justify-end">
          <Drawer.Popup
            data-slot="right-panel"
            className={cn(
              "flex h-full w-full flex-col overflow-hidden bg-card outline-none",
              "transition-transform duration-200 [transform:translateX(var(--drawer-swipe-movement-x))]",
              "data-starting-style:translate-x-full data-ending-style:translate-x-full",
              sizeClassName[size],
              className
            )}
          >
            <div className="flex items-start justify-between gap-3 border-b p-6">
              <Drawer.Title className="min-w-0 truncate font-heading text-base font-medium">
                {title}
              </Drawer.Title>
              <Drawer.Close
                aria-label="Close"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
              >
                <X className="size-4" />
              </Drawer.Close>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-6">{children}</div>

            {footer && <div className="flex flex-col-reverse gap-2 border-t bg-muted/50 p-4 sm:flex-row sm:justify-end">{footer}</div>}
          </Drawer.Popup>
        </Drawer.Viewport>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

// Convenience footer action button -- most panels just need a single
// primary submit button in the footer slot, same as DialogFooter's usage.
function RightPanelFooterButton(props: React.ComponentProps<typeof Button>) {
  return <Button className="w-full sm:w-auto" {...props} />;
}

export { RightPanel, RightPanelFooterButton };
