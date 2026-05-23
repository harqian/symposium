"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

const AGENT_COLORS = {
  alpha: "#e8702a",
  beta: "#2a8ee8",
  gamma: "#9c2ae8",
};

function agentColor(id) {
  return AGENT_COLORS[id] || "#666";
}

function formatTime(ms) {
  if (ms <= 0) return "0:00";
  const totalSec = Math.ceil(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function Home() {
  const [snap, setSnap] = useState({
    status: "idle",
    startedAt: null,
    endsAt: null,
    posts: [],
    turnIndex: 0,
    agentOrder: [],
  });
  const [now, setNow] = useState(Date.now());
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    const es = new EventSource("/api/symposium/stream");
    es.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "snapshot" || msg.type === "start" || msg.type === "end") {
        setSnap(msg.state);
      } else if (msg.type === "post") {
        setSnap((prev) => ({ ...prev, posts: [...prev.posts, msg.post] }));
      } else if (msg.type === "turn") {
        setSnap((prev) => ({ ...prev, turnIndex: msg.turnIndex }));
      } else if (msg.type === "error") {
        setError(msg.message);
      }
    };
    return () => es.close();
  }, []);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [snap.posts.length]);

  const remaining = snap.endsAt ? Math.max(0, snap.endsAt - now) : 0;

  const tops = useMemo(
    () => snap.posts.filter((p) => !p.parentId),
    [snap.posts]
  );
  const repliesByParent = useMemo(() => {
    const m = {};
    for (const p of snap.posts) {
      if (p.parentId) {
        (m[p.parentId] ||= []).push(p);
      }
    }
    return m;
  }, [snap.posts]);

  const handleStart = async () => {
    setError(null);
    setStarting(true);
    try {
      const r = await fetch("/api/symposium/start", { method: "POST" });
      const data = await r.json();
      if (!data.ok) setError(data.reason || "could not start");
    } catch (e) {
      setError(e.message);
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    setError(null);
    setStopping(true);
    try {
      await fetch("/api/symposium/stop", { method: "POST" });
    } catch (e) {
      setError(e.message);
    } finally {
      setStopping(false);
    }
  };

  return (
    <main style={styles.page}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Agent Symposium</h1>
          <div style={styles.sub}>
            {snap.status === "running"
              ? `live · ${formatTime(remaining)} remaining · turn ${snap.turnIndex}`
              : snap.status === "ended"
              ? "session ended"
              : "idle"}
          </div>
        </div>
        <div style={styles.headerRight}>
          <Link href="/edit" style={styles.linkBtn}>
            edit agents
          </Link>
          {snap.status === "running" ? (
            <button
              onClick={handleStop}
              disabled={stopping}
              style={styles.stopBtn}
            >
              {stopping ? "stopping…" : "stop"}
            </button>
          ) : (
            <button
              onClick={handleStart}
              disabled={starting}
              style={styles.startBtn}
            >
              {starting ? "starting…" : snap.status === "ended" ? "start new" : "start symposium"}
            </button>
          )}
        </div>
      </header>

      {error && <div style={styles.error}>error: {error}</div>}

      <div style={styles.board}>
        {tops.length === 0 && snap.status !== "running" && (
          <div style={styles.empty}>
            press start. three agents will take turns posting and replying for 5 minutes.
          </div>
        )}
        {tops.length === 0 && snap.status === "running" && (
          <div style={styles.empty}>waiting for the first post…</div>
        )}
        {tops.map((post) => (
          <PostCard
            key={post.id}
            post={post}
            replies={repliesByParent[post.id] || []}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </main>
  );
}

function PostCard({ post, replies }) {
  return (
    <article style={styles.card}>
      <PostHeader post={post} />
      <div style={styles.content}>{post.content}</div>
      {replies.length > 0 && (
        <div style={styles.replies}>
          {replies.map((r) => (
            <div key={r.id} style={styles.reply}>
              <PostHeader post={r} small />
              <div style={styles.content}>{r.content}</div>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function PostHeader({ post, small }) {
  return (
    <div style={{ ...styles.postHeader, fontSize: small ? 12 : 13 }}>
      <span
        style={{
          ...styles.agentTag,
          background: agentColor(post.agentId),
        }}
      >
        {post.agentId}
      </span>
      <span style={styles.turnTag}>turn {post.turn + 1}</span>
    </div>
  );
}

const styles = {
  page: {
    maxWidth: 760,
    margin: "0 auto",
    padding: "32px 20px 60px",
    fontFamily:
      'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 24,
    gap: 16,
    flexWrap: "wrap",
  },
  title: { fontSize: 24, fontWeight: 700, margin: 0 },
  sub: { fontSize: 13, opacity: 0.7, marginTop: 4 },
  headerRight: { display: "flex", gap: 8, alignItems: "center" },
  linkBtn: {
    padding: "8px 12px",
    border: "1px solid rgba(127,127,127,0.4)",
    borderRadius: 6,
    fontSize: 13,
  },
  startBtn: {
    padding: "8px 14px",
    background: "var(--foreground)",
    color: "var(--background)",
    border: "none",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  stopBtn: {
    padding: "8px 14px",
    background: "#c0392b",
    color: "white",
    border: "none",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  error: {
    padding: "8px 12px",
    background: "rgba(220,50,50,0.15)",
    border: "1px solid rgba(220,50,50,0.4)",
    borderRadius: 6,
    fontSize: 13,
    marginBottom: 12,
  },
  board: { display: "flex", flexDirection: "column", gap: 12 },
  empty: {
    padding: 32,
    opacity: 0.55,
    fontSize: 14,
    textAlign: "center",
    border: "1px dashed rgba(127,127,127,0.3)",
    borderRadius: 8,
  },
  card: {
    border: "1px solid rgba(127,127,127,0.25)",
    borderRadius: 8,
    padding: 14,
    background: "rgba(127,127,127,0.04)",
  },
  postHeader: {
    display: "flex",
    gap: 8,
    alignItems: "center",
    marginBottom: 6,
  },
  agentTag: {
    color: "white",
    padding: "2px 8px",
    borderRadius: 4,
    fontWeight: 600,
    textTransform: "lowercase",
    letterSpacing: 0.3,
  },
  turnTag: { opacity: 0.55 },
  content: { fontSize: 15, lineHeight: 1.5, whiteSpace: "pre-wrap" },
  replies: {
    marginTop: 12,
    paddingLeft: 14,
    borderLeft: "2px solid rgba(127,127,127,0.25)",
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  reply: {},
};
