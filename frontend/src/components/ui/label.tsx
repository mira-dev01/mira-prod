"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

type LabelProps = React.ComponentProps<"label"> & {
  size?: "sm" | "default"
  /** Appends a destructive-colored asterisk. Purely visual -- pair with the
   *  field's own `required`/`aria-required` for actual validation. */
  required?: boolean
}

function Label({ className, size = "default", required, children, ...props }: LabelProps) {
  return (
    <label
      data-slot="label"
      data-size={size}
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50 data-[size=sm]:text-xs",
        className
      )}
      {...props}
    >
      {children}
      {required && (
        <span className="text-destructive" aria-hidden="true">
          *
        </span>
      )}
    </label>
  )
}

export { Label }
