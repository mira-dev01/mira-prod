"use client";

import { useState } from "react";
import type { DateRange } from "react-day-picker";
import { CalendarIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDateRange } from "@/hooks/use-date-range";

function formatRange(start: Date, end: Date): string {
  const sameYear = start.getFullYear() === end.getFullYear();
  const startFmt = start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const endFmt = end.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  return sameYear ? `${startFmt} – ${endFmt}` : `${startFmt}, ${start.getFullYear()} – ${endFmt}`;
}

export function DateRangePicker() {
  const { startDate, endDate, setRange } = useDateRange();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange | undefined>({ from: startDate, to: endDate });

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setDraft({ from: startDate, to: endDate });
      }}
    >
      <PopoverTrigger
        render={
          <Button variant="outline" size="sm">
            <CalendarIcon data-icon="inline-start" />
            {formatRange(startDate, endDate)}
          </Button>
        }
      />
      <PopoverContent className="w-auto p-0">
        <Calendar
          mode="range"
          selected={draft}
          onSelect={setDraft}
          numberOfMonths={2}
          defaultMonth={startDate}
        />
        <div className="flex items-center justify-end gap-2 border-t p-2.5">
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!draft?.from || !draft?.to}
            onClick={() => {
              if (draft?.from && draft?.to) {
                setRange(draft.from, draft.to);
                setOpen(false);
              }
            }}
          >
            Apply
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
