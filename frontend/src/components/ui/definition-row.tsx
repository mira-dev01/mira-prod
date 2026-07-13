import { cn } from "@/lib/utils";

/**
 * A label/value row for compact key-fact displays (pricing quote
 * breakdowns, account-detail summaries). Not a table -- no header, no
 * borders between rows; the parent supplies its own spacing (e.g.
 * `space-y-1`) between multiple rows.
 */
function DefinitionRow({
  label,
  value,
  className,
}: {
  label: string;
  value: string | number;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export { DefinitionRow };
