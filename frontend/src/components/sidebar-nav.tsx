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
  { href: "/dashboard/guests", label: "Guests" },
  { href: "/dashboard/pricing", label: "Pricing" },
  { href: "/dashboard/technicians", label: "Technicians" },
  { href: "/dashboard/settings", label: "Settings" },
];

export function SidebarNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r bg-background p-4">
      <div className="mb-6 px-2">
        <span className="text-lg font-semibold tracking-tight">MIRA</span>
        <p className="text-xs text-muted-foreground">Host dashboard</p>
      </div>
      <nav className="flex flex-1 flex-col gap-1">
        {links.map((link) => {
          const active = link.href === "/dashboard" ? pathname === link.href : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-muted",
                active ? "bg-muted text-foreground" : "text-muted-foreground"
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
