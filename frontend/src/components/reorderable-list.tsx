"use client";

import { useEffect, useRef, useState } from "react";
import { Reorder, useDragControls } from "framer-motion";
import { ChevronDown, ChevronUp, GripVertical } from "lucide-react";
import { cn } from "@/lib/utils";

type FocusTarget = { id: string; control: "handle" | "up" | "down" };

type ReorderableListProps<T extends { id: string }> = {
  items: T[];
  onReorder: (next: T[]) => void;
  /** e.g. "navigation item" -- used in screen-reader labels. */
  itemNoun: string;
  itemLabel: (item: T) => string;
  renderItem: (item: T) => React.ReactNode;
  className?: string;
};

/**
 * Drag-and-drop list built on framer-motion's Reorder (already a
 * dependency -- no new DnD library). Every reorder path is available:
 * - pointer/touch: drag only from the grip handle (dragListener=false), so
 *   a row's other controls never start a drag and nothing navigates;
 * - keyboard: ArrowUp/ArrowDown on the focused handle;
 * - explicit move up/down buttons, for screen readers and small screens.
 * Focus follows the moved row and each move is announced politely.
 */
export function ReorderableList<T extends { id: string }>({
  items,
  onReorder,
  itemNoun,
  itemLabel,
  renderItem,
  className,
}: ReorderableListProps<T>) {
  const [announcement, setAnnouncement] = useState("");
  const controlRefs = useRef(new Map<string, HTMLButtonElement | null>());
  const pendingFocus = useRef<FocusTarget | null>(null);

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    controlRefs.current.get(`${target.id}:${target.control}`)?.focus();
  }, [items]);

  function move(id: string, delta: number, control: FocusTarget["control"]) {
    const from = items.findIndex((i) => i.id === id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    pendingFocus.current = { id, control };
    onReorder(next);
    setAnnouncement(`${itemLabel(moved)} moved to position ${to + 1} of ${items.length}.`);
  }

  const byId = new Map(items.map((i) => [i.id, i]));

  return (
    <>
      <Reorder.Group
        axis="y"
        values={items.map((i) => i.id)}
        onReorder={(ids: string[]) => onReorder(ids.map((id) => byId.get(id)!).filter(Boolean))}
        className={cn("space-y-1", className)}
      >
        {items.map((item, index) => (
          <ReorderableRow
            key={item.id}
            id={item.id}
            label={itemLabel(item)}
            itemNoun={itemNoun}
            isFirst={index === 0}
            isLast={index === items.length - 1}
            onMove={(delta, control) => move(item.id, delta, control)}
            registerControl={(control, el) => controlRefs.current.set(`${item.id}:${control}`, el)}
            onDragEnd={() => setAnnouncement(`${itemLabel(item)} dropped.`)}
          >
            {renderItem(item)}
          </ReorderableRow>
        ))}
      </Reorder.Group>
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </>
  );
}

function ReorderableRow({
  id,
  label,
  itemNoun,
  isFirst,
  isLast,
  onMove,
  registerControl,
  onDragEnd,
  children,
}: {
  id: string;
  label: string;
  itemNoun: string;
  isFirst: boolean;
  isLast: boolean;
  onMove: (delta: number, control: FocusTarget["control"]) => void;
  registerControl: (control: FocusTarget["control"], el: HTMLButtonElement | null) => void;
  onDragEnd: () => void;
  children: React.ReactNode;
}) {
  const controls = useDragControls();

  return (
    <Reorder.Item
      value={id}
      dragListener={false}
      dragControls={controls}
      onDragEnd={onDragEnd}
      className="relative flex items-center gap-1 rounded-lg border bg-card px-1 py-1"
      whileDrag={{ scale: 1.02, boxShadow: "var(--shadow-md)", zIndex: 10 }}
    >
      <button
        type="button"
        ref={(el) => registerControl("handle", el)}
        aria-label={`Reorder ${label}. Drag, or use the up and down arrow keys.`}
        aria-roledescription={`draggable ${itemNoun}`}
        className="flex size-8 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:cursor-grabbing"
        onPointerDown={(e) => {
          // Keep the gesture to this list -- e.g. the mobile nav drawer's
          // own swipe-to-close must not also react to a reorder drag.
          e.stopPropagation();
          controls.start(e);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp") {
            e.preventDefault();
            onMove(-1, "handle");
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            onMove(1, "handle");
          }
        }}
      >
        <GripVertical className="size-4" />
      </button>
      <div className="min-w-0 flex-1">{children}</div>
      <div className="flex shrink-0 flex-col">
        <MoveButton
          direction="up"
          label={label}
          disabled={isFirst}
          onClick={() => onMove(-1, "up")}
          refCallback={(el) => registerControl("up", el)}
        />
        <MoveButton
          direction="down"
          label={label}
          disabled={isLast}
          onClick={() => onMove(1, "down")}
          refCallback={(el) => registerControl("down", el)}
        />
      </div>
    </Reorder.Item>
  );
}

function MoveButton({
  direction,
  label,
  disabled,
  onClick,
  refCallback,
}: {
  direction: "up" | "down";
  label: string;
  disabled: boolean;
  onClick: () => void;
  refCallback: (el: HTMLButtonElement | null) => void;
}) {
  const Icon = direction === "up" ? ChevronUp : ChevronDown;
  // aria-disabled rather than disabled: a disabled button drops focus, which
  // would strand keyboard users after moving an item to the top/bottom.
  return (
    <button
      type="button"
      ref={refCallback}
      aria-label={`Move ${label} ${direction}`}
      aria-disabled={disabled}
      onClick={() => !disabled && onClick()}
      className={cn(
        "flex h-6 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
        disabled && "cursor-default opacity-30 hover:bg-transparent"
      )}
    >
      <Icon className="size-3.5" />
    </button>
  );
}
