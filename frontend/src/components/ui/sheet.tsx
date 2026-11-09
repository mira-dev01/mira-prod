import { cn } from "@/lib/utils";

/**
 * The dashboard layout system ("Visible Grid"): a Sheet is a 12-column grid
 * with a faint construction line on every column (globals.css .field-grid);
 * a Cell is one ruled block that spans whole columns, so every edge lands on
 * a line. Cells in a row share borders and stretch to the same height --
 * nothing floats, nothing is a different size by accident.
 *
 *   <Sheet>
 *     <Cell className="md:col-span-4">…</Cell>   // a third of the row
 *     <WideCell>                                  // full width, internal rows
 *       <SheetRow title="Email" hint="…">…</SheetRow>
 *     </WideCell>
 *   </Sheet>
 *
 * Ink rules (border-foreground) separate blocks; soft rules (border-border)
 * separate rows inside a block.
 */

export function Sheet({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("field-grid grid grid-cols-12 border-t border-l border-foreground", className)} {...props} />;
}

/** One block. Defaults to full width; narrow it with md:/lg: col-span-* classes. */
export function Cell({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("col-span-12 flex min-w-0 flex-col gap-3 border-r border-b border-foreground bg-card/70 p-5", className)}
      {...props}
    />
  );
}

/** A Cell that hosts the Card parts (CardHeader / CardContent), for the
 * dashboard cards that also appear outside a Sheet: same look inside the grid
 * (shared ruled edges, equal row height) without rewriting each card. */
export function CellCard({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <Cell
      className={cn("gap-(--card-spacing) p-0 py-(--card-spacing) [--card-spacing:--spacing(5)]", className)}
      {...props}
    />
  );
}

/** The strip above a list or table: search, filters and view switches on one
 * ruled row that shares the Sheet's edges. */
export function Toolbar({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "col-span-12 flex flex-wrap items-center gap-x-4 gap-y-3 border-r border-b border-foreground bg-card/70 px-5 py-3",
        className
      )}
      {...props}
    />
  );
}

/** A full-width block with no padding of its own, built from SheetRows that
 * sit on the same 12 columns as the rest of the sheet. */
export function WideCell({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("col-span-12 grid grid-cols-subgrid border-r border-b border-foreground bg-card/70", className)}
      {...props}
    />
  );
}

/** A block's name: small, bold, tracked caps. Carries the section; never competes with values. */
export function CellTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return <h2 className={cn("text-xs leading-5 font-bold tracking-[0.1em] text-foreground uppercase", className)} {...props} />;
}

/** The one-line plain-language purpose of a block. */
export function CellLead({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("text-sm leading-5 font-medium text-foreground", className)} {...props} />;
}

/** Supporting detail under a lead; muted, never smaller than 13px. */
export function Hint({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("text-[13px] leading-5 text-muted-foreground", className)} {...props} />;
}

/** Level 1: a block's heading (sentence case, 20px) with its one-line purpose
 * beneath. Used where a block holds several rows (e.g. Notifications), so the
 * block reads as the parent of the row names below it. */
export function SheetHeading({ title, children, className }: { title: string; children?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("col-span-12 flex flex-col gap-1 border-b border-border p-5", className)}>
      <h2 className="text-xl leading-7 font-bold tracking-tight text-foreground">{title}</h2>
      {children && <Hint>{children}</Hint>}
    </div>
  );
}

/** The fine print of a block, pinned to its bottom behind a soft rule so
 * blocks in a row fill to the same height instead of leaving blank space. The
 * shared minimum height (wide screens) puts the rule on one line across a row. */
export function CellFoot({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("-mx-5 -mb-5 mt-auto flex flex-col justify-center gap-1 border-t border-border px-5 py-3 lg:min-h-[6.5rem]", className)} {...props} />;
}

/**
 * A row inside a WideCell: the name and a one-line hint on columns 1-3, the
 * controls on columns 4-12. Rows are separated by a soft rule.
 */
export function SheetRow({
  title,
  hint,
  children,
  className,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("col-span-12 grid grid-cols-subgrid border-t border-border first:border-t-0", className)}>
      <div className="col-span-12 flex flex-col gap-1 p-5 pb-0 md:col-span-3 md:pb-5 lg:col-span-4">
        <CellTitle className="text-[13px]">{title}</CellTitle>
        {hint && <Hint>{hint}</Hint>}
      </div>
      <div className="col-span-12 flex min-w-0 flex-col gap-4 p-5 md:col-span-9 lg:col-span-8">{children}</div>
    </section>
  );
}

/** A footer strip for a WideCell: right-aligned actions on a soft rule. */
export function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("col-span-12 flex items-center justify-end gap-3 border-t border-border p-4", className)} {...props} />;
}
