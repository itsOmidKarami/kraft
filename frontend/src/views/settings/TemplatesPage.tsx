import { useEffect, useMemo, useState } from "react";
import { Check } from "@phosphor-icons/react";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { MiniChain, SectionLabel } from "../../components/ui";
import type { TemplateValidation } from "../../types";
import { PageHead, useResource } from "./shared";

/* ── 5b chain templates ───────────────────────────────────────────────────── */

export function TemplatesPage() {
  const { value, error, reload } = useResource(() => api.getTemplates());
  const templates = useMemo(() => value ?? [], [value]);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [report, setReport] = useState<TemplateValidation | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const current = templates.find((t) => t.id === selected) ?? templates[0];
  useEffect(() => {
    if (current && current.id !== selected) setSelected(current.id);
  }, [current, selected]);

  const original = useMemo(
    () => (current ? JSON.stringify(current.nodes, null, 2) : ""),
    [current],
  );
  useEffect(() => setDraft(original), [original]);

  const parsed = useMemo(() => {
    try {
      const nodes = JSON.parse(draft || "[]");
      return Array.isArray(nodes) ? nodes : null;
    } catch {
      return null;
    }
  }, [draft]);

  const check = async () => {
    if (!current || !parsed) return;
    setReport(await api.validateTemplate(current.id, parsed));
  };

  const save = async () => {
    if (!current || !parsed) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTemplate(current.id, parsed);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title="Chain templates"
        note="a live work item keeps the chain it materialized at intake; edits affect new items"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="template-editor">
        <div className="template-list">
          <SectionLabel>Templates</SectionLabel>
          {templates.map((t) => (
            <button
              key={t.id}
              className="facet-opt"
              aria-pressed={t.id === current?.id}
              onClick={() => setSelected(t.id)}
            >
              {t.id}
              <span className="facet-count">
                {t.nodes.length} nodes · {t.gates} gates
              </span>
            </button>
          ))}
        </div>
        <div className="template-draft">
          <label className="field-hint" htmlFor="template-nodes">
            nodes · validated against the registry before it is written
          </label>
          <textarea
            id="template-nodes"
            aria-label="template nodes"
            className="input mono template-yaml desktop-only"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <pre className="template-readout phone-only">{draft}</pre>
          <p className="phone-only open-on-desktop">Open on desktop to edit.</p>
          {!parsed && <p className="form-error">not valid JSON — nothing will be saved</p>}
          {/* The diagram tracks what is typed: `parsed` is the draft, so it
              updates as the textarea changes and vanishes into the "not valid
              JSON" line below when it cannot be read. No graph library — a
              chain is a list with gates, and MiniChain already draws one. */}
          {parsed && (
            <MiniChain
              nodes={parsed}
              invalid={(report?.unresolved ?? []).map((u) => u.node)}
              size="lg"
            />
          )}
          {showDiff && <DraftDiff before={original} after={draft} />}
          {report && (
            <div className="validation" data-valid={report.valid}>
              <span>{report.valid ? "valid" : report.error}</span>
              {report.unresolved.map((u) => (
                <span key={`${u.node}/${u.task}`} className="row-sub">
                  {u.node}: {u.task} does not resolve
                </span>
              ))}
            </div>
          )}
          <div className="save-row desktop-only">
            <button className="btn btn-primary" disabled={busy || !parsed} onClick={save}>
              <Check size={14} />
              Save
            </button>
            <button className="btn btn-secondary" disabled={!parsed} onClick={check}>
              Validate
            </button>
            <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
              Changes
            </button>
            <button className="btn btn-ghost" onClick={() => setDraft(original)}>
              Discard
            </button>
            <span className="save-hint">
              {message ?? `writes ${current?.id ?? "the template"}.yaml · validator re-runs`}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
