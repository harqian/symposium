import { subscribe } from "@/lib/symposium";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const encoder = new TextEncoder();
  let unsub;
  let heartbeat;

  const stream = new ReadableStream({
    start(controller) {
      const send = (event) => {
        const data = `data: ${JSON.stringify(event)}\n\n`;
        try {
          controller.enqueue(encoder.encode(data));
        } catch {
          // controller closed
        }
      };
      unsub = subscribe(send);
      heartbeat = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(`: ping\n\n`));
        } catch {
          clearInterval(heartbeat);
        }
      }, 15000);
    },
    cancel() {
      if (unsub) unsub();
      if (heartbeat) clearInterval(heartbeat);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
