import Link from "next/link";
import { notFound } from "next/navigation";
import { MiraLogo } from "@/components/brand/mira-logo";
import { Cell, Sheet } from "@/components/ui/sheet";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Photos shown per property on this page; "See all photos" opens the full gallery.
const PREVIEW_PHOTOS = 4;

interface PropertyGallery {
  id: string;
  name: string;
  city: string | null;
  photos: string[];
}

// The page a guest reaches from the WhatsApp message "here are the stays we talked about": the properties the
// agent recommended on their call (?ids=a,b,c), each with its photos. Public, no sign-in, like /p/{id}/photos.
async function getGalleries(ids: string): Promise<PropertyGallery[]> {
  const res = await fetch(`${API_BASE_URL}/properties/gallery?ids=${encodeURIComponent(ids)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load properties (${res.status})`);
  return res.json();
}

export default async function PropertyListPage({
  searchParams,
}: {
  searchParams: Promise<{ ids?: string }>;
}) {
  const { ids } = await searchParams;
  if (!ids) notFound();
  const properties = await getGalleries(ids);
  if (properties.length === 0) notFound();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-foreground px-4 py-4 md:px-8">
        <MiraLogo className="h-8 text-foreground" />
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8 md:px-8 md:py-10">
        <h1 className="page-title text-3xl text-foreground">The stays we talked about</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {properties.length} propert{properties.length === 1 ? "y" : "ies"}
        </p>

        {properties.map((property) => (
          <section key={property.id} className="mt-10">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <h2 className="text-xl font-medium text-foreground">{property.name}</h2>
                {property.city && <p className="text-sm text-muted-foreground">{property.city}</p>}
              </div>
              {property.photos.length > PREVIEW_PHOTOS && (
                <Link
                  href={`/p/${property.id}/photos`}
                  className="text-sm underline underline-offset-2 hover:text-foreground"
                >
                  See all {property.photos.length} photos
                </Link>
              )}
            </div>

            <Sheet className="mt-4">
              {property.photos.length === 0 ? (
                <Cell>
                  <p className="text-sm text-muted-foreground">No photos are available for this property yet.</p>
                </Cell>
              ) : (
                property.photos.slice(0, PREVIEW_PHOTOS).map((url, i) => (
                  <Cell key={url} className="gap-0 p-0 md:col-span-6">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={url}
                      alt={`${property.name} photo ${i + 1}`}
                      loading="lazy"
                      className="aspect-[4/3] w-full object-cover"
                    />
                  </Cell>
                ))
              )}
            </Sheet>
          </section>
        ))}
      </main>
    </div>
  );
}
