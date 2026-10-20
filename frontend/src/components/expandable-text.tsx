"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

type ExpandableTextProps = {
  text: string;
  maxLength?: number;
  className?: string;
};

export function ExpandableText({ text, maxLength = 140, className }: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > maxLength;

  if (!isLong) {
    return <span className={cn("whitespace-pre-wrap break-words", className)}>{text}</span>;
  }

  return (
    <span className={cn("whitespace-pre-wrap break-words", className)}>
      {expanded ? text : `${text.slice(0, maxLength).trimEnd()}…`}{" "}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        className="font-medium text-foreground hover:underline"
      >
        {expanded ? "Show less" : "Read more"}
      </button>
    </span>
  );
}
