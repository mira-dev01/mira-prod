"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { AudioLines } from "lucide-react";
import { toast } from "sonner";
import { api, API_BASE_URL, API_WRITE_EVENT, getToken } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Opens the backend browser-test page (GET /api/v1/voice/test) in a new tab. The call runs as
 * the signed-in host: the backend resolves their portfolio, knowledge base and agent settings
 * from the token, so no other host's data is ever reachable from here.
 */
async function openAgentTest() {
  const token = await getToken();
  if (!token) {
    toast.error("Not logged in");
    return;
  }
  const backendOrigin = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
  window.open(`${backendOrigin}/api/v1/voice/test?${new URLSearchParams({ token }).toString()}`, "_blank");
}

/**
 * Floating "Test your agent" pin, shown on every dashboard page. While the host has changed
 * something the agent says or does and not yet tested it (GET /agent-config/test-status), the
 * pin is highlighted. The status is re-read after any save, on page change, and when the tab
 * regains focus (which is where the host returns to after a test call).
 */
export function TestAgentPin() {
  const [busy, setBusy] = useState(false);
  const [needsTest, setNeedsTest] = useState(false);
  const pathname = usePathname();
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(() => {
    window.clearTimeout(timer.current);
    // Debounced: a save that fires several requests re-checks once, after they settle.
    timer.current = window.setTimeout(() => {
      api.agentConfig
        .testStatus()
        .then((status) => setNeedsTest(status.needs_test))
        .catch(() => {
          // Keep the last known state; the pin still works without the highlight.
        });
    }, 600);
  }, []);

  useEffect(() => {
    refresh();
  }, [pathname, refresh]);

  useEffect(() => {
    window.addEventListener(API_WRITE_EVENT, refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener(API_WRITE_EVENT, refresh);
      window.removeEventListener("focus", refresh);
      window.clearTimeout(timer.current);
    };
  }, [refresh]);

  async function handleClick() {
    setBusy(true);
    try {
      await openAgentTest();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed right-4 bottom-4 z-40 md:right-6 md:bottom-6">
      {needsTest && (
        <p
          role="status"
          className="absolute right-0 bottom-full mb-2 w-max max-w-56 rounded-lg border border-primary/30 bg-card px-3 py-1.5 text-xs text-foreground shadow-sm"
        >
          You changed your agent. Test it.
        </p>
      )}
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={cn(
          "relative flex items-center gap-2 rounded-full border px-4 py-2.5 text-sm font-semibold shadow-md transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60",
          needsTest
            ? "border-primary bg-primary text-primary-foreground hover:bg-primary/90"
            : "border-border bg-card text-heading hover:bg-accent"
        )}
      >
        <AudioLines className={cn("size-4", needsTest ? "text-primary-foreground" : "text-primary")} aria-hidden />
        Test your agent
        {needsTest && (
          <span aria-hidden className="absolute -top-1 -right-1 flex size-3">
            <span className="absolute inline-flex size-full rounded-full bg-primary/60 motion-safe:animate-ping" />
            <span className="relative inline-flex size-3 rounded-full border-2 border-card bg-primary" />
          </span>
        )}
      </button>
    </div>
  );
}
