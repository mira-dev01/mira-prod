import { redirect } from "next/navigation";

// Opportunities became the "Missed calls" tab of the Calls page. This route
// stays so old links, bookmarks and notifications still land somewhere right.
export default function OpportunitiesRedirect() {
  redirect("/dashboard/calls?tab=missed");
}
