"use client"

import * as React from "react"
import { Loader2, Mic, Square } from "lucide-react"

import { Textarea } from "@/components/ui/textarea"
import { useDictation } from "@/hooks/use-dictation"
import { cn } from "@/lib/utils"

type DictationTextareaProps = Omit<React.ComponentProps<typeof Textarea>, "onChange"> & {
  /** Plain string setter, e.g. `(v) => onChange({ ...form, field: v })` --
   *  called both on typing (same as a normal onChange, just unwrapped to
   *  the string already) and after a dictated clip is transcribed. */
  onValueChange: (value: string) => void
}

// Drop-in replacement for Textarea that adds a mic button: click to record,
// click again to stop, then the clip is transcribed (app/api/v1/voice.py's
// /voice/transcribe, same Sarvam batch STT as the FAQ-gap voice-answer flow)
// and appended to whatever's already typed -- dictation augments typing
// rather than replacing it, so a host can type part of a policy/description
// and dictate the rest. Takes onValueChange (a plain string setter) instead
// of onChange so appending dictated text doesn't need to fake a React
// ChangeEvent -- every call site already does `(e) => set(e.target.value)`
// one way or another, so this just moves that unwrapping into the caller.
function DictationTextarea({ className, onValueChange, value, ...props }: DictationTextareaProps) {
  const applyText = React.useCallback(
    (dictated: string) => {
      const current = typeof value === "string" ? value : ""
      const next = current && !current.endsWith(" ") ? `${current} ${dictated}` : `${current}${dictated}`
      onValueChange(next)
    },
    [value, onValueChange]
  )

  const { status, toggle } = useDictation(applyText)

  return (
    <div className="relative">
      <Textarea
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        className={cn("pr-9", className)}
        {...props}
      />
      <button
        type="button"
        onClick={toggle}
        disabled={status === "transcribing"}
        aria-label={status === "recording" ? "Stop dictation" : "Dictate"}
        title={status === "recording" ? "Stop dictation" : "Dictate"}
        className={cn(
          "absolute right-2 top-2 flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50",
          status === "recording" && "text-destructive hover:text-destructive"
        )}
      >
        {status === "transcribing" ? (
          <Loader2 className="size-4 animate-spin" />
        ) : status === "recording" ? (
          <Square className="size-3.5 fill-current" />
        ) : (
          <Mic className="size-4" />
        )}
      </button>
    </div>
  )
}

export { DictationTextarea }
