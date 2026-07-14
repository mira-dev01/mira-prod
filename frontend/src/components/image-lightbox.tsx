"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Keyed by src from the parent so mounting fresh per slide naturally resets
 * the fade-in, without needing an effect to reset state on index change. */
function LightboxImage({ src, alt }: { src: string; alt: string }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <>
      {!loaded && <div className="absolute size-8 animate-pulse rounded-full bg-white/10" />}
      <img
        src={src}
        alt={alt}
        onLoad={() => setLoaded(true)}
        className={cn(
          // pointer-events-auto so right-click (open/copy image) still works on the
          // image itself, but its bounding box shouldn't swallow clicks meant for
          // the prev/next buttons layered on top at the edges.
          "max-h-full max-w-full rounded-md object-contain transition-opacity",
          loaded ? "opacity-100" : "opacity-0"
        )}
      />
    </>
  );
}

/**
 * Fullscreen image slider. Deliberately renders a plain <img> (not a CSS
 * background or next/image fill trick) so the browser's native right-click
 * menu offers "Open image in new tab" / "Copy image" for free -- no custom
 * context menu needed.
 */
export function ImageLightbox({
  photos,
  index,
  onIndexChange,
  open,
  onOpenChange,
  title,
}: {
  photos: string[];
  index: number;
  onIndexChange: (index: number) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
}) {
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "ArrowLeft") goPrev();
      if (e.key === "ArrowRight") goNext();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, index, photos.length]);

  function goPrev() {
    onIndexChange((index - 1 + photos.length) % photos.length);
  }

  function goNext() {
    onIndexChange((index + 1) % photos.length);
  }

  if (photos.length === 0) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className="flex h-[90vh] w-full max-w-6xl flex-col gap-3 border-none bg-black/95 p-0 text-white shadow-none ring-0 sm:max-w-6xl [&_button[data-slot=dialog-close]]:text-white [&_button[data-slot=dialog-close]]:hover:bg-white/10"
      >
        <div className="flex min-h-0 flex-1 items-center justify-center px-4 pt-10">
          <div className="relative flex h-full w-full items-center justify-center">
            <LightboxImage key={photos[index]} src={photos[index]} alt={title ? `${title} — photo ${index + 1}` : `Photo ${index + 1}`} />

            {photos.length > 1 && (
              <>
                <button
                  type="button"
                  aria-label="Previous photo"
                  onClick={goPrev}
                  className="absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white hover:bg-black/60"
                >
                  <ChevronLeft className="size-6" />
                </button>
                <button
                  type="button"
                  aria-label="Next photo"
                  onClick={goNext}
                  className="absolute right-0 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white hover:bg-black/60"
                >
                  <ChevronRight className="size-6" />
                </button>
              </>
            )}
          </div>
        </div>

        {photos.length > 1 && (
          <div className="flex shrink-0 items-center justify-center gap-2 pb-4">
            <div className="flex max-w-full gap-1.5 overflow-x-auto px-4">
              {photos.map((photo, i) => (
                <button
                  key={photo + i}
                  type="button"
                  aria-label={`Go to photo ${i + 1}`}
                  onClick={() => onIndexChange(i)}
                  className={cn(
                    "size-12 shrink-0 overflow-hidden rounded-md ring-2 transition-opacity",
                    i === index ? "opacity-100 ring-white" : "opacity-50 ring-transparent hover:opacity-80"
                  )}
                >
                  <img src={photo} alt="" className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          </div>
        )}

        <p className="pb-2 text-center text-xs text-white/60">
          {index + 1} / {photos.length}
        </p>
      </DialogContent>
    </Dialog>
  );
}
