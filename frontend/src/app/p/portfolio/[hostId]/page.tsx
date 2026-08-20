import Link from "next/link";
import { notFound } from "next/navigation";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

interface PropertyGallery {
  id: string;
  name: string;
  city: string | null;
  photos: string[];
}

async function getPortfolioGallery(hostId: string): Promise<PropertyGallery[] | null> {
  const res = await fetch(`${API_BASE_URL}/properties/portfolio/${hostId}/gallery`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to load portfolio gallery (${res.status})`);
  return res.json();
}

export default async function PortfolioPhotosPage({
  params,
}: {
  params: Promise<{ hostId: string }>;
}) {
  const { hostId } = await params;
  const properties = await getPortfolioGallery(hostId);

  if (properties === null) notFound();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-5">
        <span className="brand-logo text-3xl text-primary">mira</span>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-10">
        <h1 className="page-title text-3xl text-foreground">Our properties</h1>

        {properties.length === 0 ? (
          <p className="mt-8 text-muted-foreground">No properties are available yet.</p>
        ) : (
          <div className="mt-8 space-y-10">
            {properties.map((property) => (
              <section key={property.id}>
                <Link href={`/p/${property.id}/photos`} className="hover:underline">
                  <h2 className="text-xl font-semibold text-foreground">{property.name}</h2>
                </Link>
                {property.city && (
                  <p className="mt-1 text-sm text-muted-foreground">{property.city}</p>
                )}

                {property.photos.length === 0 ? (
                  <p className="mt-3 text-sm text-muted-foreground">No photos available yet.</p>
                ) : (
                  <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {property.photos.slice(0, 6).map((url, i) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        key={url}
                        src={url}
                        alt={`${property.name} photo ${i + 1}`}
                        loading="lazy"
                        className="aspect-square w-full rounded-xl border border-border object-cover"
                      />
                    ))}
                  </div>
                )}
              </section>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
