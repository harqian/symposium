import { stopSymposium } from "@/lib/symposium";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  const result = stopSymposium();
  return Response.json(result, { status: result.ok ? 200 : 409 });
}
