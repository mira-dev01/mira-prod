"use client"

import * as React from "react"
import { Loader2, Mic, Square } from "lucide-react"

import { Input } from "@/components/ui/input"
import { useDictation } from "@/hooks/use-dictation"

type DictationInputProps = Omit<React.ComponentProps<typeof Input>, "onChange" | "trailingIcon"> & {
  /** Plain string setter -- see DictationTextarea for why this replaces onChange. */
  onValueChange: (value: string) => void
}

// Input counterpart to DictationTextarea -- see that component for the
// record/transcribe/append design. Uses Input's existing trailingIcon slot
// for the mic button rather than adding new positioning of its own.
function DictationInput({ onValueChange, value, ...props }: DictationInputProps) {
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
    <Input
      value={value}
      onChange={(e) => onValueChange(e.target.value)}
      trailingIcon={
        <button
          type="button"
          onClick={toggle}
          disabled={status === "transcribing"}
          aria-label={status === "recording" ? "Stop dictation" : "Dictate"}
          title={status === "recording" ? "Stop dictation" : "Dictate"}
          className="flex items-center justify-center rounded-md p-0.5 transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === "transcribing" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : status === "recording" ? (
            <Square className="size-3.5 fill-current text-destructive" />
          ) : (
            <Mic className="size-4" />
          )}
        </button>
      }
      {...props}
    />
  )
}

export { DictationInput }
