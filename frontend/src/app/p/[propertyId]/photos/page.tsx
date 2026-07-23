import { notFound } from "next/navigation";

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
      <header className="border-b border-border px-6 py-5">
        <span className="brand-logo text-3xl text-primary">mira</span>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-10">
        <h1 className="page-title text-3xl text-foreground">{gallery.name}</h1>
        {gallery.city && (
          <p className="mt-1 text-sm text-muted-foreground">{gallery.city}</p>
        )}

        {gallery.photos.length === 0 ? (
          <p className="mt-8 text-muted-foreground">
            No photos are available for this property yet.
          </p>
        ) : (
          <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {gallery.photos.map((url, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={url}
                src={url}
                alt={`${gallery.name} photo ${i + 1}`}
                loading="lazy"
                className="w-full rounded-xl border border-border object-cover"
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
