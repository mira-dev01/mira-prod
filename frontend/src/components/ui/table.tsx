"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      className="relative w-full overflow-x-auto"
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({
  className,
  sticky = false,
  ...props
}: React.ComponentProps<"thead"> & {
  /** Pins the header to the top of its scroll container while the body scrolls
   *  underneath. Opt-in: the consumer must give the table's wrapping
   *  container a bounded height (e.g. `max-h-[...] overflow-y-auto`) for this
   *  to do anything -- on an unbounded page-scroll table (the default for
   *  every table in the app today) it's a no-op. Needs an opaque background
   *  (uses --card, matching the header row's usual card context) so body
   *  rows don't show through while pinned. */
  sticky?: boolean
}) {
  return (
    <thead
      data-slot="table-header"
      data-sticky={sticky}
      className={cn(
        "[&_tr]:border-b",
        sticky && "sticky top-0 z-10 bg-card [&_tr]:bg-card",
        className
      )}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        // font-size/font-weight for this element are owned by the
        // [data-slot="table-head"] rule in globals.css (New Title,
        // --font-subheading) -- not set here, so that rule's values apply
        // instead of a Tailwind utility fighting it at equal specificity.
        "h-10 px-2 text-left align-middle whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
