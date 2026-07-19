"use client";

import { motion } from "framer-motion";

// Subtle end-of-hero affordance -- signals there's more below without
// competing with the headline or the animated showcase above it.
export function ScrollCue() {
  return (
    <div className="relative z-10 hidden justify-center pb-10 lg:flex">
      <motion.div
        className="flex flex-col items-center gap-2 text-muted-foreground"
        animate={{ y: [0, 6, 0] }}
        transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
      >
        <span className="text-micro">Scroll to explore</span>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M8 3v10M8 13l-4-4M8 13l4-4"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </motion.div>
    </div>
  );
}
