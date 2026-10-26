"use client";

import Link from "next/link";
import { ArrowRight, Building2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { USE_CASES } from "@/lib/use-cases";
import { cn, glassCardClassName } from "@/lib/utils";
import type { UseCaseKey } from "@/lib/types";

function ChecklistRow({
  index,
  icon: Icon,
  label,
  href,
}: {
  index: number;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  href: string;
}) {
  return (
    <li>
      <Link
        href={href}
        className="flex items-center gap-3 rounded-xl px-3 py-3 transition-colors hover:bg-accent/50"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
          {index}
        </span>
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 text-sm font-medium">{label}</span>
        <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
      </Link>
    </li>
  );
}

/**
 * Overview's true first-run state: a host with zero properties sees every
 * other widget's own soft empty state ("--", "nothing pending", "you're
 * all caught up") rendered at once, which is five empty cards, not a
 * simpler page. This replaces that grid with a short, personalized
 * checklist instead -- "add a property" always first, then one line per
 * use-case interest picked in onboarding Step 0 (frontend/src/lib/
 * use-cases.ts), so day one points at what this specific host said they
 * came here for.
 */
export function FirstRunChecklist({ name, interests }: { name: string | null | undefined; interests: UseCaseKey[] }) {
  const selected = USE_CASES.filter((useCase) => interests.includes(useCase.key));

  return (
    <Card className={cn(glassCardClassName)}>
      <CardHeader>
        <CardTitle className="page-title text-xl">Welcome to Mira{name ? `, ${name}` : ""}</CardTitle>
        <p className="text-sm text-muted-foreground">Let&apos;s get your first property answering calls.</p>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1">
          <ChecklistRow index={1} icon={Building2} label="Add your first property" href="/dashboard/properties" />
          {selected.map((useCase, i) => (
            <ChecklistRow
              key={useCase.key}
              index={i + 2}
              icon={useCase.icon}
              label={`Set up ${useCase.label.toLowerCase()}`}
              href={useCase.navHref}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
