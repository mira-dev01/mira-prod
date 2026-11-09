"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { CardContent, CardHeader } from "@/components/ui/card";
import { CellCard } from "@/components/ui/sheet";
import { USE_CASES } from "@/lib/use-cases";
import type { UseCaseKey } from "@/lib/types";

function ChecklistRow({ index, label, href }: { index: number; label: string; href: string }) {
  return (
    <li className="border-b border-border first:pt-0 last:border-0">
      <Link href={href} className="flex items-center gap-3 py-3 transition-colors hover:text-primary">
        <span className="w-5 shrink-0 text-sm font-semibold text-muted-foreground tabular-nums">{index}</span>
        <span className="min-w-0 flex-1 text-sm font-medium">{label}</span>
        <ArrowRight className="size-3.5 shrink-0" />
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
    <CellCard className="md:col-span-8 lg:col-span-6">
      <CardHeader>
        <h2 className="text-xl leading-7 font-bold tracking-tight">Welcome to Mira{name ? `, ${name}` : ""}</h2>
        <p className="text-[13px] text-muted-foreground">Let&apos;s get your first property answering calls.</p>
      </CardHeader>
      <CardContent>
        <ul>
          <ChecklistRow index={1} label="Add your first property" href="/dashboard/properties" />
          {selected.map((useCase, i) => (
            <ChecklistRow
              key={useCase.key}
              index={i + 2}
              label={`Set up ${useCase.label.toLowerCase()}`}
              href={useCase.navHref}
            />
          ))}
        </ul>
      </CardContent>
    </CellCard>
  );
}
