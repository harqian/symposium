import { startSymposium } from "@/lib/symposium";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  const result = await startSymposium();
  return Response.json(result, { status: result.ok ? 200 : 409 });
}
