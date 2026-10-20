import * as React from "react"

import { cn } from "@/lib/utils"

type TextareaProps = React.ComponentProps<"textarea"> & {
  /** Inline validation message shown below the field. Implies aria-invalid + error styling
   *  even if aria-invalid wasn't explicitly passed -- same convention as Input. */
  errorMessage?: string
}

function Textarea({ className, errorMessage, ...props }: TextareaProps) {
  const invalid = errorMessage ? true : props["aria-invalid"]

  const field = (
    <textarea
      data-slot="textarea"
      aria-invalid={invalid}
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )

  if (!errorMessage) return field

  return (
    <div className="w-full">
      {field}
      <p className="mt-1.5 text-xs text-destructive">{errorMessage}</p>
    </div>
  )
}

export { Textarea }
