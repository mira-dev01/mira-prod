import Link from "next/link";
import { notFound } from "next/navigation";
import { MiraLogo } from "@/components/brand/mira-logo";
import { Cell, Sheet } from "@/components/ui/sheet";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Photos shown per property on this page; "See all photos" opens that property's full gallery.
const PREVIEW_PHOTOS = 4;

interface PropertyGallery {
  id: string;
  name: string;
  city: string | null;
  photos: string[];
}

// One link that shows EVERY stay a host has, each in its own section with its photos, grouped by city: what a host
// sends a guest who wants to see everything at once. Public, no sign-in, like /p/{id}/photos and /p/list.
// ?city=Bengaluru narrows it to one city.
async function getGalleries(hostId: string, city: string | undefined): Promise<PropertyGallery[]> {
  const query = city ? `?city=${encodeURIComponent(city)}` : "";
  const res = await fetch(`${API_BASE_URL}/properties/gallery/host/${encodeURIComponent(hostId)}${query}`, {
    cache: "no-store",
  });
  if (res.status === 404 || res.status === 422) return [];
  if (!res.ok) throw new Error(`Failed to load properties (${res.status})`);
  return res.json();
}

function groupByCity(properties: PropertyGallery[]): { city: string; properties: PropertyGallery[] }[] {
  const groups = new Map<string, { city: string; properties: PropertyGallery[] }>();
  for (const property of properties) {
    const city = property.city?.trim() || "Other";
    const key = city.toLowerCase();
    const group = groups.get(key) ?? { city, properties: [] };
    group.properties.push(property);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export default async function HostGalleryPage({
  params,
  searchParams,
}: {
  params: Promise<{ hostId: string }>;
  searchParams: Promise<{ city?: string }>;
}) {
  const { hostId } = await params;
  const { city } = await searchParams;
  const properties = await getGalleries(hostId, city);
  if (properties.length === 0) notFound();

  const groups = groupByCity(properties);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-foreground px-4 py-4 md:px-8">
        <MiraLogo className="h-8 text-foreground" />
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8 md:px-8 md:py-10">
        <h1 className="page-title text-3xl text-foreground">{city ? `Our stays in ${city}` : "All our stays"}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {properties.length} propert{properties.length === 1 ? "y" : "ies"}
          {groups.length > 1 ? ` in ${groups.length} places` : ""}
        </p>

        {/* A quick index, so a long list can be jumped through. */}
        <nav aria-label="Properties" className="mt-6 space-y-4">
          {groups.map((group) => (
            <div key={group.city}>
              <p className="text-sm font-medium text-foreground">{group.city}</p>
              <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
                {group.properties.map((property) => (
                  <li key={property.id}>
                    <a href={`#${property.id}`} className="underline underline-offset-2 hover:text-foreground">
                      {property.name}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        {groups.map((group) => (
          <div key={group.city} className="mt-12">
            <h2 className="border-b border-foreground pb-2 text-2xl font-medium text-foreground">{group.city}</h2>

            {group.properties.map((property) => (
              <section key={property.id} id={property.id} className="mt-8 scroll-mt-6">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-xl font-medium text-foreground">{property.name}</h3>
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
                  {property.photos.slice(0, PREVIEW_PHOTOS).map((url, i) => (
                    <Cell key={url} className="gap-0 p-0 md:col-span-6">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={url}
                        alt={`${property.name} photo ${i + 1}`}
                        loading="lazy"
                        className="aspect-[4/3] w-full object-cover"
                      />
                    </Cell>
                  ))}
                </Sheet>
              </section>
            ))}
          </div>
        ))}
      </main>
    </div>
  );
}
