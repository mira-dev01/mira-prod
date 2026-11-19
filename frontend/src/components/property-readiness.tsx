import type { PropertyReadiness } from "@/lib/property-readiness";

/**
 * "What Mira knows" for one property card: a short bar and the first things still missing. Shown only while
 * something is missing -- a complete property says nothing, so the cards that need attention stand out.
 */
export function PropertyReadinessLine({ readiness }: { readiness: PropertyReadiness }) {
  if (readiness.complete) return null;
  const shown = readiness.missing.slice(0, 3);
  const more = readiness.missing.length - shown.length;
  return (
    <div className="space-y-1.5" role="group" aria-label={`Mira knows ${readiness.doneCount} of ${readiness.total}`}>
      <div className="flex items-center gap-2">
        <div className="flex flex-1 gap-0.5" aria-hidden>
          {readiness.items.map((item, i) => (
            <div key={item.key} className={`h-1 flex-1 ${i < readiness.doneCount ? "bg-foreground" : "bg-border"}`} />
          ))}
        </div>
        <span className="shrink-0 text-xs font-semibold tabular-nums">
          {readiness.doneCount}/{readiness.total}
        </span>
      </div>
      <p className="text-[13px] text-muted-foreground">
        Add {shown.join(", ").toLowerCase()}
        {more > 0 ? ` +${more}` : ""}
      </p>
    </div>
  );
}
