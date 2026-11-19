import { redirect } from "next/navigation";

// Calendar is a tab of the Properties page now. Old links and bookmarks land there.
export default function CalendarRedirect(): never {
  redirect("/dashboard/properties?tab=calendar");
}
