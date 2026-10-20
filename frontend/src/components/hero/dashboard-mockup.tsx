// Static, non-interactive abstraction of the real dashboard's shape (stat
// tiles + a list) used purely as a blurred backdrop behind the hero's
// animated workflow cards -- deliberately not the real StatCard/Card data
// components, since this never fetches data or needs auth.
export function DashboardMockup() {
  return (
    <div
      aria-hidden
      className="absolute inset-0 rounded-3xl bg-card p-6 opacity-70 blur-[0.5px]"
      style={{ boxShadow: "var(--shadow-md)" }}
    >
      <div className="flex items-center justify-between">
        <div className="h-3 w-24 rounded-full bg-foreground/10" />
        <div className="h-6 w-6 rounded-full bg-foreground/10" />
      </div>

      <div className="mt-5 grid grid-cols-3 gap-3">
        {["--status-live", "--status-pending", "--status-progress"].map((v) => (
          <div key={v} className="rounded-xl bg-muted/60 p-3 ring-1 ring-foreground/5">
            <div
              className="mb-3 h-5 w-5 rounded-full"
              style={{ backgroundColor: `color-mix(in oklch, var(${v}) 28%, transparent)` }}
            />
            <div className="h-2 w-10 rounded-full bg-foreground/15" />
            <div className="mt-2 h-3 w-14 rounded-full bg-foreground/20" />
          </div>
        ))}
      </div>

      <div className="mt-auto space-y-2.5 pt-40">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-3 rounded-lg bg-muted/40 p-2.5">
            <div className="h-7 w-7 shrink-0 rounded-full bg-foreground/10" />
            <div className="flex-1 space-y-1.5">
              <div className="h-2 w-3/5 rounded-full bg-foreground/10" />
              <div className="h-2 w-2/5 rounded-full bg-foreground/5" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
