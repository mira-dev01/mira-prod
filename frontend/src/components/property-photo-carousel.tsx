"use client";

import { useState } from "react";
import { Building2, ChevronLeft, ChevronRight } from "lucide-react";
import { ImageLightbox } from "@/components/image-lightbox";
import { cn } from "@/lib/utils";

/** Compact cover-image carousel for a property card -- click opens the full
 * ImageLightbox slider. Hover/tap arrows just cycle the card's cover photo,
 * they don't open the viewer (matches common gallery-card UX, e.g. Airbnb's
 * own listing cards). */
export function PropertyPhotoCarousel({ photos, name }: { photos: string[]; name: string }) {
  const [index, setIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  if (photos.length === 0) {
    return (
      <div className="flex h-36 w-full items-center justify-center bg-muted">
        <Building2 className="size-8 text-muted-foreground" />
      </div>
    );
  }

  function goPrev(e: React.MouseEvent) {
    e.stopPropagation();
    setIndex((i) => (i - 1 + photos.length) % photos.length);
  }

  function goNext(e: React.MouseEvent) {
    e.stopPropagation();
    setIndex((i) => (i + 1) % photos.length);
  }

  return (
    <>
      <div className="group relative h-36 w-full overflow-hidden bg-muted">
        <button
          type="button"
          className="block h-full w-full cursor-zoom-in"
          onClick={() => setLightboxOpen(true)}
          aria-label={`View photos of ${name}`}
        >
          <img src={photos[index]} alt={name} className="h-36 w-full object-cover" />
        </button>

        {photos.length > 1 && (
          <>
            <button
              type="button"
              aria-label={`Previous photo of ${name}`}
              onClick={goPrev}
              className="absolute left-1.5 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-1 text-white opacity-0 transition-opacity group-hover:opacity-100"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              type="button"
              aria-label={`Next photo of ${name}`}
              onClick={goNext}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-1 text-white opacity-0 transition-opacity group-hover:opacity-100"
            >
              <ChevronRight className="size-4" />
            </button>
            <div className="absolute bottom-1.5 left-1/2 flex -translate-x-1/2 gap-1">
              {photos.map((_, i) => (
                <span
                  key={i}
                  className={cn(
                    "size-1.5 rounded-full transition-colors",
                    i === index ? "bg-white" : "bg-white/40"
                  )}
                />
              ))}
            </div>
            <span className="absolute right-1.5 top-1.5 rounded-full bg-black/50 px-1.5 py-0.5 text-[0.65rem] text-white">
              {index + 1}/{photos.length}
            </span>
          </>
        )}
      </div>

      <ImageLightbox
        photos={photos}
        index={index}
        onIndexChange={setIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        title={name}
      />
    </>
  );
}
