import { redirect } from "next/navigation";

// The Agent page moved to /dashboard/agent. This route stays so old links and
// bookmarks still land in the right place.
export default function AiTrainingRedirect() {
  redirect("/dashboard/agent");
}
