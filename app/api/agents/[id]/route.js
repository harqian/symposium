import { loadAgent, saveAgent } from "@/lib/agents";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req, { params }) {
  const { id } = await params;
  try {
    const agent = await loadAgent(id);
    return Response.json(agent);
  } catch {
    return Response.json({ error: "not found" }, { status: 404 });
  }
}

export async function PUT(req, { params }) {
  const { id } = await params;
  const body = await req.json();
  const agent = await saveAgent(id, {
    system: body.system,
    context: body.context,
    instructions: body.instructions,
  });
  return Response.json(agent);
}
