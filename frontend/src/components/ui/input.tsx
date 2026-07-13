import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

type InputProps = Omit<React.ComponentProps<"input">, "size"> & {
  /** Visual size variant. Not the native HTML `size` (character-width) attribute. */
  size?: "sm" | "default"
  /** Icon rendered inside the field, left-aligned. Sized/colored automatically. */
  leadingIcon?: React.ReactNode
  /** Icon rendered inside the field, right-aligned (e.g. a clear button, unit label). */
  trailingIcon?: React.ReactNode
  /** Inline validation message shown below the field. Implies aria-invalid + error styling
   *  even if aria-invalid wasn't explicitly passed. */
  errorMessage?: string
}

function Input({
  className,
  type,
  size = "default",
  leadingIcon,
  trailingIcon,
  errorMessage,
  ...props
}: InputProps) {
  const invalid = errorMessage ? true : props["aria-invalid"]

  const field = (
    <InputPrimitive
      type={type}
      data-slot="input"
      data-size={size}
      aria-invalid={invalid}
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-[size=sm]:h-7 data-[size=sm]:rounded-[min(var(--radius-md),10px)]",
        leadingIcon && "pl-8",
        trailingIcon && "pr-8",
        className
      )}
      {...props}
    />
  )

  if (!leadingIcon && !trailingIcon && !errorMessage) return field

  return (
    <div className="w-full">
      <div className="relative">
        {leadingIcon && (
          <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-muted-foreground [&_svg]:size-4">
            {leadingIcon}
          </span>
        )}
        {field}
        {trailingIcon && (
          <span className="absolute inset-y-0 right-2.5 flex items-center text-muted-foreground [&_svg]:size-4">
            {trailingIcon}
          </span>
        )}
      </div>
      {errorMessage && <p className="mt-1.5 text-xs text-destructive">{errorMessage}</p>}
    </div>
  )
}

export { Input }
