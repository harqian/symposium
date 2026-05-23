import { getClient, MODEL } from "./cerebras.js";
import { loadAgent, listAgentIds } from "./agents.js";

const DURATION_MS = 5 * 60 * 1000;
const TURN_INTERVAL_MS = 5000;

function freshState() {
  return {
    status: "idle",
    startedAt: null,
    endsAt: null,
    posts: [],
    turnIndex: 0,
    agentOrder: [],
    subscribers: new Set(),
    runId: 0,
  };
}

globalThis.__symposium ??= freshState();
const state = globalThis.__symposium;

function broadcast(event) {
  for (const sub of state.subscribers) {
    try {
      sub(event);
    } catch {
      state.subscribers.delete(sub);
    }
  }
}

export function subscribe(fn) {
  state.subscribers.add(fn);
  fn({ type: "snapshot", state: publicState() });
  return () => state.subscribers.delete(fn);
}

export function publicState() {
  return {
    status: state.status,
    startedAt: state.startedAt,
    endsAt: state.endsAt,
    posts: state.posts,
    turnIndex: state.turnIndex,
    agentOrder: state.agentOrder,
  };
}

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

function formatBoard(posts) {
  if (posts.length === 0) return "(The board is empty. You will be the first to post.)";
  const tops = posts.filter((p) => !p.parentId);
  const lines = [];
  for (const top of tops) {
    lines.push(`POST ${top.id} — by ${top.agentId} (turn ${top.turn})`);
    lines.push(`  "${top.content}"`);
    const replies = posts.filter((p) => p.parentId === top.id);
    for (const r of replies) {
      lines.push(`  └─ reply by ${r.agentId} (turn ${r.turn}): "${r.content}"`);
    }
    lines.push("");
  }
  return lines.join("\n");
}

async function generatePost(agentId, turn) {
  const agent = await loadAgent(agentId);
  const board = formatBoard(state.posts);
  const userPrompt = [
    `# Context`,
    agent.context,
    ``,
    `# Current board`,
    board,
    ``,
    `# Your live instructions (these may have just been updated)`,
    agent.instructions,
    ``,
    `# Your task`,
    `It is your turn (turn ${turn + 1}). Decide whether to create a new top-level post or reply to one of the posts above.`,
    `Respond with a single JSON object and nothing else:`,
    `{"action": "post" | "reply", "parentId": "<id>" | null, "content": "<your text>"}`,
    `- "post" means a new top-level idea. parentId must be null.`,
    `- "reply" means you are responding to a specific existing post. parentId must be the id of one of the posts listed.`,
    `- content is plain prose, under 80 words, in your voice.`,
  ].join("\n");

  const client = getClient();
  const resp = await client.chat.completions.create({
    model: MODEL,
    messages: [
      { role: "system", content: agent.system },
      { role: "user", content: userPrompt },
    ],
    response_format: { type: "json_object" },
    max_tokens: 500,
    temperature: 0.9,
  });

  const raw = resp.choices?.[0]?.message?.content ?? "";
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const content = typeof parsed.content === "string" ? parsed.content.trim() : "";
  if (!content) return null;

  let parentId = null;
  if (parsed.action === "reply" && typeof parsed.parentId === "string") {
    if (state.posts.some((p) => p.id === parsed.parentId)) {
      parentId = parsed.parentId;
    }
  }

  return {
    id: newId(),
    agentId,
    parentId,
    content,
    turn,
    createdAt: Date.now(),
  };
}

async function loop(runId) {
  while (
    state.status === "running" &&
    state.runId === runId &&
    Date.now() < state.endsAt
  ) {
    const agentId = state.agentOrder[state.turnIndex % state.agentOrder.length];
    const turn = state.turnIndex;
    const tickStart = Date.now();
    try {
      const post = await generatePost(agentId, turn);
      if (state.runId !== runId) return;
      if (post) {
        state.posts.push(post);
        broadcast({ type: "post", post });
      }
    } catch (err) {
      broadcast({
        type: "error",
        message: `agent ${agentId}: ${err.message || String(err)}`,
      });
    }
    state.turnIndex++;
    broadcast({ type: "turn", turnIndex: state.turnIndex });

    const elapsed = Date.now() - tickStart;
    const wait = Math.max(0, TURN_INTERVAL_MS - elapsed);
    await new Promise((r) => setTimeout(r, wait));
  }
  if (state.runId === runId) {
    state.status = "ended";
    broadcast({ type: "end", state: publicState() });
  }
}

export async function startSymposium() {
  if (state.status === "running") {
    return { ok: false, reason: "already running" };
  }
  const ids = await listAgentIds();
  if (ids.length === 0) {
    return { ok: false, reason: "no agents found" };
  }
  state.runId += 1;
  state.status = "running";
  state.startedAt = Date.now();
  state.endsAt = state.startedAt + DURATION_MS;
  state.posts = [];
  state.turnIndex = 0;
  state.agentOrder = ids;
  broadcast({ type: "start", state: publicState() });
  loop(state.runId);
  return { ok: true };
}

export function stopSymposium() {
  if (state.status !== "running") return { ok: false };
  state.status = "ended";
  state.runId += 1;
  broadcast({ type: "end", state: publicState() });
  return { ok: true };
}
