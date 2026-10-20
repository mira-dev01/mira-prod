"use client";

import { useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ImagePlus, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ImageLightbox } from "@/components/image-lightbox";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Photo management grid for the property edit dialog: view all photos (via
 * the shared ImageLightbox slider), reorder, delete, set cover, and upload
 * new ones. Uploads go straight to the backend (POST /properties/{id}/photos
 * -> Cloudinary) and append to `photos` immediately -- unlike the rest of
 * the edit form, there's no "unsaved" staging state for uploads/deletes,
 * since an uploaded file has to land somewhere server-side right away.
 * Reordering the array itself IS staged via onChange, saved with the rest
 * of the form on submit.
 */
export function PropertyPhotosManager({
  propertyId,
  photos,
  onChange,
  propertyName,
}: {
  propertyId: string;
  photos: string[];
  onChange: (next: string[]) => void;
  propertyName: string;
}) {
  const [uploading, setUploading] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length === 0) return;

    setUploading(true);
    try {
      let latestPhotos = photos;
      for (const file of files) {
        const updated = await api.properties.uploadPhoto(propertyId, file);
        latestPhotos = updated.photos;
        onChange(latestPhotos);
      }
      toast.success(files.length > 1 ? `${files.length} photos added` : "Photo added");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload photo");
    } finally {
      setUploading(false);
    }
  }

  function removeAt(index: number) {
    onChange(photos.filter((_, i) => i !== index));
  }

  function moveTo(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= photos.length) return;
    const next = [...photos];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  function setCover(index: number) {
    if (index === 0) return;
    const next = [...photos];
    const [photo] = next.splice(index, 1);
    next.unshift(photo);
    onChange(next);
  }

  return (
    <div className="space-y-3">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
        multiple
        className="hidden"
        onChange={handleUpload}
      />

      {photos.length === 0 ? (
        <p className="text-xs text-muted-foreground">No photos yet — add some below.</p>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {photos.map((photo, index) => (
            <div
              key={photo + index}
              className="group relative aspect-square overflow-hidden rounded-md border bg-muted"
            >
              <button
                type="button"
                className="block h-full w-full cursor-zoom-in"
                onClick={() => setLightboxIndex(index)}
                aria-label={`View photo ${index + 1}`}
              >
                <img src={photo} alt={`${propertyName} photo ${index + 1}`} className="h-full w-full object-cover" />
              </button>

              {index === 0 && (
                <span className="absolute left-1 top-1 rounded-full bg-primary px-1.5 py-0.5 text-[0.6rem] font-medium text-primary-foreground">
                  Cover
                </span>
              )}

              <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-0.5 bg-gradient-to-t from-black/70 to-transparent p-1 opacity-0 transition-opacity group-hover:opacity-100">
                <div className="flex gap-0.5">
                  <button
                    type="button"
                    aria-label="Move left"
                    disabled={index === 0}
                    onClick={() => moveTo(index, -1)}
                    className="rounded p-1 text-white hover:bg-white/20 disabled:pointer-events-none disabled:opacity-30"
                  >
                    <ChevronLeft className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label="Move right"
                    disabled={index === photos.length - 1}
                    onClick={() => moveTo(index, 1)}
                    className="rounded p-1 text-white hover:bg-white/20 disabled:pointer-events-none disabled:opacity-30"
                  >
                    <ChevronRight className="size-3.5" />
                  </button>
                </div>
                <div className="flex gap-0.5">
                  {index !== 0 && (
                    <button
                      type="button"
                      aria-label="Set as cover photo"
                      onClick={() => setCover(index)}
                      className="rounded p-1 text-white hover:bg-white/20"
                    >
                      <Star className="size-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label="Delete photo"
                    onClick={() => removeAt(index)}
                    className="rounded p-1 text-white hover:bg-destructive/80"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={uploading}
        onClick={() => fileInputRef.current?.click()}
        className={cn("gap-1.5")}
      >
        <ImagePlus className="size-3.5" />
        {uploading ? "Uploading…" : "Add photos"}
      </Button>

      {lightboxIndex !== null && (
        <ImageLightbox
          photos={photos}
          index={lightboxIndex}
          onIndexChange={setLightboxIndex}
          open={lightboxIndex !== null}
          onOpenChange={(open) => !open && setLightboxIndex(null)}
          title={propertyName}
        />
      )}
    </div>
  );
}
