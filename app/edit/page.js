"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export default function EditPage() {
  const [agents, setAgents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState({ system: "", context: "", instructions: "" });
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then(({ agents }) => {
        setAgents(agents);
        if (agents.length && !selectedId) {
          setSelectedId(agents[0].id);
          setDraft(pick(agents[0]));
        }
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectAgent = (id) => {
    const a = agents.find((x) => x.id === id);
    if (!a) return;
    setSelectedId(id);
    setDraft(pick(a));
    setSavedAt(null);
    setError(null);
  };

  const save = async () => {
    if (!selectedId) return;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch(`/api/agents/${selectedId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!r.ok) throw new Error(`save failed: ${r.status}`);
      const updated = await r.json();
      setAgents((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setSavedAt(Date.now());
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <main style={styles.page}>
      <header style={styles.header}>
        <h1 style={styles.title}>edit agents</h1>
        <Link href="/" style={styles.linkBtn}>
          ← back to symposium
        </Link>
      </header>

      <div style={styles.tabs}>
        {agents.map((a) => (
          <button
            key={a.id}
            onClick={() => selectAgent(a.id)}
            style={{
              ...styles.tab,
              ...(a.id === selectedId ? styles.tabActive : {}),
            }}
          >
            {a.id}
          </button>
        ))}
      </div>

      {selectedId && (
        <div style={styles.form}>
          <Field
            label="system"
            help="identity, never changes during the symposium"
            value={draft.system}
            onChange={(v) => setDraft((d) => ({ ...d, system: v }))}
            rows={6}
          />
          <Field
            label="context"
            help="situational background — the room, the other agents"
            value={draft.context}
            onChange={(v) => setDraft((d) => ({ ...d, context: v }))}
            rows={5}
          />
          <Field
            label="instructions"
            help="live-editable directives — take effect on this agent's next turn"
            value={draft.instructions}
            onChange={(v) => setDraft((d) => ({ ...d, instructions: v }))}
            rows={5}
          />
          <div style={styles.row}>
            <button onClick={save} disabled={saving} style={styles.saveBtn}>
              {saving ? "saving…" : "save"}
            </button>
            {savedAt && (
              <span style={styles.savedTag}>
                saved {new Date(savedAt).toLocaleTimeString()}
              </span>
            )}
            {error && <span style={styles.errorTag}>{error}</span>}
          </div>
        </div>
      )}
    </main>
  );
}

function Field({ label, help, value, onChange, rows }) {
  return (
    <label style={styles.field}>
      <div style={styles.fieldHeader}>
        <span style={styles.fieldLabel}>{label}</span>
        <span style={styles.fieldHelp}>{help}</span>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        style={styles.textarea}
      />
    </label>
  );
}

function pick(a) {
  return {
    system: a.system || "",
    context: a.context || "",
    instructions: a.instructions || "",
  };
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
    alignItems: "center",
    marginBottom: 20,
  },
  title: { fontSize: 22, fontWeight: 700, margin: 0 },
  linkBtn: {
    padding: "8px 12px",
    border: "1px solid rgba(127,127,127,0.4)",
    borderRadius: 6,
    fontSize: 13,
  },
  tabs: { display: "flex", gap: 6, marginBottom: 18 },
  tab: {
    padding: "6px 14px",
    border: "1px solid rgba(127,127,127,0.35)",
    background: "transparent",
    color: "inherit",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 13,
    textTransform: "lowercase",
  },
  tabActive: {
    background: "var(--foreground)",
    color: "var(--background)",
    borderColor: "var(--foreground)",
  },
  form: { display: "flex", flexDirection: "column", gap: 18 },
  field: { display: "flex", flexDirection: "column", gap: 6 },
  fieldHeader: { display: "flex", justifyContent: "space-between", alignItems: "baseline" },
  fieldLabel: { fontSize: 13, fontWeight: 600, textTransform: "lowercase" },
  fieldHelp: { fontSize: 11, opacity: 0.6 },
  textarea: {
    width: "100%",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 13,
    lineHeight: 1.5,
    padding: 10,
    border: "1px solid rgba(127,127,127,0.35)",
    borderRadius: 6,
    background: "transparent",
    color: "inherit",
    resize: "vertical",
  },
  row: { display: "flex", gap: 10, alignItems: "center" },
  saveBtn: {
    padding: "8px 16px",
    background: "var(--foreground)",
    color: "var(--background)",
    border: "none",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  savedTag: { fontSize: 12, opacity: 0.6 },
  errorTag: { fontSize: 12, color: "#e54" },
};
