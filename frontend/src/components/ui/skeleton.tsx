import { cn } from "@/lib/utils"

type SkeletonProps = React.ComponentProps<"div"> & {
  /** text: a single line matching body-text height/radius.
   *  avatar: a circle, for Avatar-shaped placeholders.
   *  block: the original bare rectangle (default) -- cards, images, tables. */
  variant?: "block" | "text" | "avatar"
}

function Skeleton({ className, variant = "block", ...props }: SkeletonProps) {
  return (
    <div
      data-slot="skeleton"
      data-variant={variant}
      className={cn(
        "animate-pulse rounded-md bg-muted",
        variant === "text" && "h-4 w-full rounded-sm",
        variant === "avatar" && "size-9 shrink-0 rounded-full",
        className
      )}
      {...props}
    />
  )
}

export { Skeleton }
