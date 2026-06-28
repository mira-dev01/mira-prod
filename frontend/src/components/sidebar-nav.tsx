"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";

const links = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/properties", label: "Properties" },
  { href: "/dashboard/calls", label: "Calls" },
  { href: "/dashboard/leads", label: "Leads" },
  { href: "/dashboard/guests", label: "Guests" },
  { href: "/dashboard/pricing", label: "Pricing" },
  { href: "/dashboard/faq", label: "FAQ" },
  { href: "/dashboard/technicians", label: "Technicians" },
  { href: "/dashboard/settings", label: "Settings" },
];

export function SidebarNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r bg-card p-4">
      <div className="mb-6 px-2">
        <span className="font-display text-2xl italic text-foreground">
          <span className="mr-1 text-[var(--accent-warm)]">✳</span>
          Mira
        </span>
        <p className="mt-0.5 text-xs text-muted-foreground">Host dashboard</p>
      </div>
      <nav className="flex flex-1 flex-col gap-1">
        <span className="text-micro px-2 pb-1 pt-2">Main</span>
        {links.map((link) => {
          const active = link.href === "/dashboard" ? pathname === link.href : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "rounded-[var(--radius)] px-3 py-2 text-sm transition-colors duration-150 hover:bg-accent hover:text-accent-foreground",
                active ? "bg-accent font-medium text-accent-foreground" : "font-normal text-muted-foreground"
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
      <div className="space-y-2 border-t pt-4">
        <p className="truncate px-2 text-xs text-muted-foreground">{user?.email}</p>
        <Button variant="outline" size="sm" className="w-full" onClick={logout}>
          Log out
        </Button>
      </div>
    </aside>
  );
}
