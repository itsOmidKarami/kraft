import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowsClockwise, Check, Flag, Shield } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { OverflowMenu, Tabs, type OverflowItem } from "../../components/ui";
import { useStore } from "../../store";
import type { ChainFile, ChainNode, TemplateSummary } from "../../types";
import "./templates.css";
import { PhoneHeader, usePhone, useResource } from "./shared";

/* ── Chains (design 27 / m13) on Template Schema V1 ──────────────────────────
 *
 * A chain is one file under `templates/chains/`, and this screen edits that
 * file's text: the pill strip shows the saved chain as it resolves
 * (`GET /templates`), the editor holds the file as its author wrote it
 * (`GET /templates/{id}`), a debounced check resolves the draft against the
 * installed library without writing anything (`POST /templates/resolve`), and
 * Save writes the text back only if the server resolves it too
 * (`PUT /templates/{id}`). There is no per-node form: a V1 node is a typed
 * `exec`/`gate` with library references, and the YAML is its one faithful
 * editor. */

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function useUsedByCounts(templates: TemplateSummary[]) {
  const items = useStore((s) => Object.values(s.workItems));
  const counts: Record<string, number> = {};
  for (const t of templates) counts[t.id] = 0;
  for (const item of items) {
    const tid = item.chain_definition?.template_id;
    if (tid && tid in counts) counts[tid] += 1;
  }
  return counts;
}

/** The saved chain's resolved nodes: a pill per node, a flag on a gate, a loop
 *  icon on a node with a fix loop, a shield on a gate with an automated
 *  review. Read-only: the chain is edited as its file. */
function NodeStrip({ nodes }: { nodes: ChainNode[] }) {
  return (
    <div className="chain-graph">
      {nodes.map((n) => (
        <span key={n.id} className="chain-graph-item">
          <span className="chain-pill">
            <span>{n.id}</span>
            <span className="chain-pill-count">{n.tasks.length}</span>
            {n.kind === "gate" && <Flag size={11} weight="fill" data-testid={`chain-flag-${n.id}`} />}
            {n.fix_loop && <ArrowsClockwise size={11} />}
            {n.auto_escalate && <Shield size={11} />}
          </span>
        </span>
      ))}
    </div>
  );
}

type DraftCheck = { valid: boolean; problems: string[] };

/** Resolve the draft against the installed library, 400ms after the last
 *  keystroke. Parsing is the server's (no YAML library ships here). */
function useDraftCheck(text: string | null): DraftCheck | null {
  const [check, setCheck] = useState<DraftCheck | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    if (text === null) {
      setCheck(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        const parsed = await api.parseTemplateYaml(text);
        const result = parsed.chain
          ? await api.resolveTemplate(parsed.chain)
          : { chains: [], issues: [{ file: "", chain: null, message: parsed.error ?? "not YAML" }] };
        if (mine !== seq.current) return; // a newer keystroke superseded this
        const problems = result.issues.map((i) => i.message);
        setCheck({ valid: problems.length === 0 && result.chains.length > 0, problems });
      } catch (e) {
        if (mine === seq.current) setCheck({ valid: false, problems: [e instanceof Error ? e.message : String(e)] });
      }
    }, 400);
    return () => clearTimeout(t);
  }, [text]);
  return check;
}

export function TemplatesPage() {
  const { value, reload } = useResource(() => api.getTemplates());
  const templates = useMemo(() => value ?? [], [value]);
  const usedBy = useUsedByCounts(templates);
  const phone = usePhone();
  const [params, setParams] = useSearchParams();

  const tplId = params.get("tpl") ?? templates[0]?.id ?? null;
  const current = templates.find((t) => t.id === tplId) ?? null;

  const [saved, setSaved] = useState<ChainFile | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [tab, setTab] = useState<"yaml" | "diff">("yaml");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const check = useDraftCheck(draft);

  useEffect(() => {
    setSaved(null);
    setDraft(null);
    setMessage(null);
    if (!tplId) return;
    let alive = true;
    api
      .getTemplate(tplId)
      .then((f) => alive && setSaved(f))
      .catch((e) => alive && setMessage(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [tplId]);

  const text = draft ?? saved?.text ?? "";
  const dirty = draft !== null && draft !== saved?.text;

  const save = async () => {
    if (!current || draft === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTemplate(current.id, draft);
      setSaved(await api.getTemplate(current.id));
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Copies the saved chain under a new, prompted name and opens it. */
  const duplicate = async () => {
    if (!current || !saved) return;
    const name = window.prompt("Duplicate as", `${current.id}-copy`);
    if (!name) return;
    if (templates.some((t) => t.id === name)) {
      setMessage(`"${name}" already exists`);
      return;
    }
    const renamed = /^id:.*$/m.test(saved.text)
      ? saved.text.replace(/^id:.*$/m, `id: ${name}`)
      : `id: ${name}\n${saved.text}`;
    try {
      await api.putTemplate(name, renamed);
      await reload();
      setParams({ tpl: name });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  if (!current) {
    return (
      <div className="chain-head">
        <h2 className="chain-head-name">Chains</h2>
        <span className="chain-head-counts">no chain templates yet</span>
      </div>
    );
  }

  const gates = current.nodes.filter((n) => n.kind === "gate").length;
  const templateItems: OverflowItem[] = [
    ...templates.map((t) => ({
      label: t.id,
      icon: <Check size={14} style={{ visibility: t.id === current.id ? "visible" : "hidden" }} aria-hidden />,
      onSelect: () => setParams({ tpl: t.id }),
    })),
    { label: "Duplicate", divider: true, onSelect: duplicate },
    { label: "Delete", disabled: true, hint: "no delete route for templates yet (Kraft-lwtco)", onSelect: () => {} },
  ];

  // A saved chain that does not resolve says why; a draft says what its own
  // check found.
  const problems = dirty ? (check?.problems ?? []) : current.error ? [current.error] : [];
  const valid = dirty ? check?.valid === true : current.error === null;

  const head = (
    <div className="chain-head">
      <h2 className="chain-head-name">{current.id}</h2>
      {valid && <span className="tag tag-accent validation-badge">valid</span>}
      <span className="chain-head-counts">
        {plural(current.nodes.length, "node")} · {plural(gates, "gate")}
      </span>
      <span className="chain-head-spacer" />
      {!phone && <OverflowMenu label="template" text items={templateItems} />}
      <button className="btn btn-ghost" disabled={!dirty} onClick={() => setDraft(null)}>
        Revert
      </button>
      <button className="btn btn-primary" disabled={busy || !dirty || check?.valid !== true} onClick={save}>
        <Check size={14} />
        Save
      </button>
      {message && <span className="save-hint">{message}</span>}
    </div>
  );

  return (
    <>
      {phone && <PhoneHeader back="Settings" backTo="/settings" title="Chains" subtitle="~/.kraft/templates/chains" />}
      <div className="chain-page">
        {phone && (
          <select
            className="input chain-template-select"
            aria-label="template"
            value={current.id}
            onChange={(e) => setParams({ tpl: e.target.value })}
          >
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.id}
              </option>
            ))}
          </select>
        )}
        {head}
        <div className="chain-graph-row">
          <NodeStrip nodes={current.nodes} />
          <p className="chain-legend">
            <Flag size={11} weight="fill" /> gate · <ArrowsClockwise size={11} /> fix loop ·{" "}
            <Shield size={11} /> automated review · used by {plural(usedBy[current.id] ?? 0, "item")}
          </p>
        </div>
        <div className="chain-yaml-pane">
          <div className="field-hint">{saved?.file ?? `chains/${current.id}.yaml`}</div>
          <Tabs
            tabs={[
              { id: "yaml", label: "yaml" },
              { id: "diff", label: "diff vs saved" },
            ]}
            value={tab}
            onChange={(id) => setTab(id as "yaml" | "diff")}
          />
          {tab === "yaml" ? (
            <textarea
              aria-label="chain yaml"
              className="input mono template-yaml"
              spellCheck={false}
              value={text}
              onChange={(e) => setDraft(e.target.value)}
            />
          ) : (
            <DraftDiff before={saved?.text ?? ""} after={text} />
          )}
        </div>
        {problems.length > 0 && (
          <div className="validation" data-valid={false}>
            {problems.map((p) => (
              <span key={p}>{p}</span>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
