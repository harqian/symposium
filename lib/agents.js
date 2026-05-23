import { promises as fs } from "fs";
import path from "path";

const AGENTS_DIR = path.join(process.cwd(), "agents");
const SECTIONS = ["system", "context", "instructions"];

export async function listAgentIds() {
  const files = await fs.readdir(AGENTS_DIR);
  return files
    .filter((f) => f.endsWith(".md"))
    .map((f) => f.replace(/\.md$/, ""))
    .sort();
}

function parseAgentMarkdown(text) {
  const out = { system: "", context: "", instructions: "" };
  const lines = text.split("\n");
  let current = null;
  let buf = [];
  const flush = () => {
    if (current) out[current] = buf.join("\n").trim();
  };
  for (const line of lines) {
    const m = line.match(/^##\s+(\w+)\s*$/);
    if (m && SECTIONS.includes(m[1].toLowerCase())) {
      flush();
      current = m[1].toLowerCase();
      buf = [];
    } else if (current) {
      buf.push(line);
    }
  }
  flush();
  return out;
}

function serializeAgentMarkdown(agent) {
  return SECTIONS.map((s) => `## ${s}\n${(agent[s] || "").trim()}\n`).join("\n");
}

export async function loadAgent(id) {
  const file = path.join(AGENTS_DIR, `${id}.md`);
  const text = await fs.readFile(file, "utf8");
  const parts = parseAgentMarkdown(text);
  return { id, ...parts };
}

export async function loadAllAgents() {
  const ids = await listAgentIds();
  return Promise.all(ids.map(loadAgent));
}

export async function saveAgent(id, parts) {
  const file = path.join(AGENTS_DIR, `${id}.md`);
  const merged = {};
  for (const s of SECTIONS) merged[s] = parts[s] ?? "";
  await fs.writeFile(file, serializeAgentMarkdown(merged), "utf8");
  return { id, ...merged };
}
