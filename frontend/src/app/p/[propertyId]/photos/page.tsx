import { notFound } from "next/navigation";
import { MiraLogo } from "@/components/brand/mira-logo";
import { Cell, Sheet } from "@/components/ui/sheet";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

interface PropertyGallery {
  id: string;
  name: string;
  city: string | null;
  photos: string[];
}

async function getGallery(propertyId: string): Promise<PropertyGallery | null> {
  const res = await fetch(`${API_BASE_URL}/properties/${propertyId}/gallery`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to load gallery (${res.status})`);
  return res.json();
}

export default async function PropertyPhotosPage({
  params,
}: {
  params: Promise<{ propertyId: string }>;
}) {
  const { propertyId } = await params;
  const gallery = await getGallery(propertyId);

  if (!gallery) notFound();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-foreground px-4 py-4 md:px-8">
        <MiraLogo className="h-8 text-foreground" />
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8 md:px-8 md:py-10">
        <h1 className="page-title text-3xl text-foreground">{gallery.name}</h1>
        {gallery.city && <p className="mt-1 text-sm text-muted-foreground">{gallery.city}</p>}

        <Sheet className="mt-8">
          {gallery.photos.length === 0 ? (
            <Cell>
              <p className="text-sm text-muted-foreground">No photos are available for this property yet.</p>
            </Cell>
          ) : (
            gallery.photos.map((url, i) => (
              <Cell key={url} className="gap-0 p-0 md:col-span-6">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={url}
                  alt={`${gallery.name} photo ${i + 1}`}
                  loading="lazy"
                  className="aspect-[4/3] w-full object-cover"
                />
              </Cell>
            ))
          )}
        </Sheet>
      </main>
    </div>
  );
}
