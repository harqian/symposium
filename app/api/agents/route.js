import { loadAllAgents } from "@/lib/agents";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const agents = await loadAllAgents();
  return Response.json({ agents });
}
