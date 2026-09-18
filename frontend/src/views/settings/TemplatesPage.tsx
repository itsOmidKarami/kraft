import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowsClockwise, Check, Flag, Shield } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { OverflowMenu, SectionLabel, Tabs, type OverflowItem } from "../../components/ui";
import { adapterOf } from "../../format";
import { useStore } from "../../store";
import type { TemplateNode, TemplateSummary, TemplateValidation } from "../../types";
import "./templates.css";
import { PhoneHeader, usePhone, useResource } from "./shared";

/* ── 5b chain templates (Chains editor, design 27 / m13; W11 · D) ────────── */

const GATE_NAMES = [
  "spec_approval",
  "plan_approval",
  "chain_finalized",
  "human_review_approval",
] as const;

/** `{id, nodes}` -> the exact YAML `write_yaml` on the server produces for
 *  this fixed shape (2-space indent, `- id: x` inline for a node's first
 *  key, one key per line after). Deliberately narrow: a chain template is
 *  `{id: str, nodes: [{...}]}`, nothing nested deeper than a node's own
 *  scalar/list values — this is not a general YAML emitter. */
export function serializeNodes(id: string, nodes: TemplateNode[]): string {
  const scalar = (v: unknown): string => {
    if (v === null || v === undefined) return "null";
    if (typeof v === "boolean") return String(v);
    if (typeof v === "number") return String(v);
    if (Array.isArray(v)) return `[${v.map((x) => String(x)).join(", ")}]`;
    return String(v);
  };
  const lines = [`id: ${id}`, "nodes:"];
  for (const node of nodes) {
    const entries = Object.entries(node);
    entries.forEach(([k, v], i) => {
      lines.push(`${i === 0 ? "  - " : "    "}${k}: ${scalar(v)}`);
    });
  }
  return lines.join("\n") + "\n";
}

/** One node's block of `serializeNodes`' output -- the editor card's YAML
 *  fragment (D.3), without the file's `id:` and `nodes:` lines. */
export function serializeFragment(node: TemplateNode): string {
  return serializeNodes("", [node]).split("\n").slice(2).join("\n");
}

/** The mirror image of `serializeNodes`, used only to prove it round-trips —
 *  not a general YAML parser, and only ever agrees with `serializeNodes`'s
 *  own output shape. Operator-typed YAML always goes through the server's
 *  `POST /templates/parse` instead (Task 4). */
export function reparseSerializedNodes(text: string): TemplateNode[] {
  const scalarBack = (raw: string): unknown => {
    if (raw === "null") return null;
    if (raw === "true") return true;
    if (raw === "false") return false;
    if (raw.startsWith("[") && raw.endsWith("]")) {
      const inner = raw.slice(1, -1).trim();
      return inner === "" ? [] : inner.split(",").map((s) => s.trim());
    }
    if (/^-?\d+$/.test(raw)) return Number(raw);
    return raw;
  };
  const body = text.replace(/^id: .*\n/, "").replace(/^nodes:\n/, "");
  const blocks = body.split(/\n(?=  - )/).filter((b) => b.trim());
  return blocks.map((block) => {
    const lines = block.split("\n").filter((l) => l.trim());
    const node: TemplateNode = { id: "", tasks: [], gate_after: null };
    lines.forEach((line) => {
      const stripped = line.replace(/^ {2}- /, "").replace(/^ {4}/, "");
      const idx = stripped.indexOf(": ");
      const key = stripped.slice(0, idx);
      const value = stripped.slice(idx + 2);
      node[key] = scalarBack(value);
    });
    return node;
  });
}

function gatesOf(nodes: TemplateNode[]) {
  return nodes.filter((n) => n.gate_after).length;
}

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

/** Node pills: `{node.id}` + task count, a flag after a gated node, a loop
 *  icon when `fix_loop` is set, a shield when `auto_escalate` — with `⊕`
 *  insert buttons between every pair and at the end. No drag-and-drop:
 *  ponytail — up/down move buttons on the selected node cover reorder in a
 *  fraction of the code; add real drag if operators ask for it. */
function NodeGraph({
  nodes,
  selected,
  onSelect,
  onInsert,
  onMove,
}: {
  nodes: TemplateNode[];
  selected: string | null;
  onSelect: (id: string) => void;
  onInsert: (index: number) => void;
  onMove: (index: number, dir: -1 | 1) => void;
}) {
  return (
    <div className="chain-graph">
      <button
        type="button"
        className="chain-insert"
        aria-label="Add node at start"
        onClick={() => onInsert(0)}
      >
        ⊕
      </button>
      {nodes.map((n, i) => (
        <span key={`${n.id}-${i}`} className="chain-graph-item">
          <button
            type="button"
            className="chain-pill"
            data-selected={n.id === selected || undefined}
            onClick={() => onSelect(n.id)}
          >
            <span>{n.id || "(unnamed)"}</span>
            <span className="chain-pill-count">{(n.tasks ?? []).length}</span>
            {n.gate_after && <Flag size={11} weight="fill" data-testid={`chain-flag-${n.id}`} />}
            {n.fix_loop && <ArrowsClockwise size={11} />}
            {n.auto_escalate && <Shield size={11} />}
          </button>
          {n.id === selected && (
            <span className="chain-move">
              <button
                type="button"
                aria-label={`move ${n.id} left`}
                disabled={i === 0}
                onClick={() => onMove(i, -1)}
              >
                ←
              </button>
              <button
                type="button"
                aria-label={`move ${n.id} right`}
                disabled={i === nodes.length - 1}
                onClick={() => onMove(i, 1)}
              >
                →
              </button>
            </span>
          )}
          <button
            type="button"
            className="chain-insert"
            aria-label={`Add node after ${n.id || i}`}
            onClick={() => onInsert(i + 1)}
          >
            ⊕
          </button>
        </span>
      ))}
    </div>
  );
}

function NodeForm({
  node,
  index,
  total,
  loopNames,
  earlierIds,
  onChange,
}: {
  node: TemplateNode;
  index: number;
  total: number;
  loopNames: string[];
  earlierIds: string[];
  onChange: (patch: Partial<TemplateNode>) => void;
}) {
  const [registry] = useResourceValue(() => api.getRegistry());
  const hooks = registry?.hooks ?? {};
  const tasks = node.tasks ?? [];
  const [addingTask, setAddingTask] = useState(false);
  const [pickedTask, setPickedTask] = useState("");
  const taskOptions = Object.keys(hooks).filter((h) => !tasks.includes(h));

  const addTask = () => {
    if (!pickedTask) return;
    onChange({ tasks: [...tasks, pickedTask] });
    setAddingTask(false);
    setPickedTask("");
  };

  return (
    <div className="chain-node-form">
      <h3 className="chain-node-title">
        {node.id || "(unnamed)"} · node {index + 1} of {total}
      </h3>
      <div className="field">
        <label htmlFor="chain-node-id">id</label>
        <input
          id="chain-node-id"
          className="input"
          value={node.id}
          onChange={(e) => onChange({ id: e.target.value })}
        />
      </div>

      {/* Kraft-7ifcj: these run concurrently (`asyncio.gather` in
          executor/dispatch.py's `measure_node`), and this editor offers no
          reorder control. The old "in order" taught the wrong execution model
          to exactly the person configuring a chain. */}
      <SectionLabel>Tasks · hook points, run concurrently</SectionLabel>
      {tasks.map((hook, i) => (
        <div key={`${hook}-${i}`} className="chain-task-row">
          <span className="mono">{hook}</span>
          <span className="row-sub">{hooks[hook] ? adapterOf(hooks[hook]) : ""}</span>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => onChange({ tasks: tasks.filter((_, j) => j !== i) })}
          >
            remove
          </button>
        </div>
      ))}
      {addingTask ? (
        <div className="chain-task-row">
          <select
            className="input"
            aria-label="hook to add"
            autoFocus
            value={pickedTask}
            onChange={(e) => setPickedTask(e.target.value)}
          >
            <option value="">choose a hook…</option>
            {taskOptions.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-primary" disabled={!pickedTask} onClick={addTask}>
            Add
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setAddingTask(false);
              setPickedTask("");
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="btn btn-secondary"
          disabled={taskOptions.length === 0}
          onClick={() => setAddingTask(true)}
        >
          + Add task
        </button>
      )}

      <div className="field">
        <label htmlFor="chain-gate-after">gate_after</label>
        <select
          id="chain-gate-after"
          className="input"
          value={node.gate_after ?? ""}
          onChange={(e) => onChange({ gate_after: e.target.value || null })}
        >
          <option value="">—</option>
          {GATE_NAMES.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="chain-fix-loop">fix_loop</label>
        <select
          id="chain-fix-loop"
          className="input"
          value={node.fix_loop ?? ""}
          onChange={(e) => onChange({ fix_loop: e.target.value || null })}
        >
          <option value="">—</option>
          {loopNames.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label>on_failure</label>
        <div className="submodules">
          {(node.on_failure ?? []).map((n) => (
            <button
              key={n}
              type="button"
              className="tag"
              onClick={() => onChange({ on_failure: (node.on_failure ?? []).filter((x) => x !== n) })}
            >
              {n} ✕
            </button>
          ))}
          <button
            type="button"
            className="tag tag-off"
            onClick={() => {
              const n = window.prompt("Node id to run on failure");
              if (n) onChange({ on_failure: [...(node.on_failure ?? []), n] });
            }}
          >
            + add
          </button>
        </div>
      </div>
      <div className="field">
        <label htmlFor="chain-reject-to">reject_to</label>
        <select
          id="chain-reject-to"
          className="input"
          value={node.reject_to ?? ""}
          onChange={(e) => onChange({ reject_to: e.target.value || null })}
        >
          <option value="">—</option>
          {earlierIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="chain-auto-escalate">auto-escalate</label>
        <button
          id="chain-auto-escalate"
          type="button"
          role="switch"
          aria-checked={!!node.auto_escalate}
          className="switch"
          onClick={() => onChange({ auto_escalate: !node.auto_escalate })}
        >
          <span className="switch-knob" />
        </button>
      </div>
      <div className="field">
        <label htmlFor="chain-auto-escalate-stuck">auto_escalate_stuck</label>
        <button
          id="chain-auto-escalate-stuck"
          type="button"
          role="switch"
          aria-checked={node.auto_escalate_stuck ?? true}
          className="switch"
          onClick={() => onChange({ auto_escalate_stuck: !(node.auto_escalate_stuck ?? true) })}
        >
          <span className="switch-knob" />
        </button>
      </div>
      <div className="field">
        <label htmlFor="chain-auto-escalate-delay">auto_escalate_delay_s</label>
        <input
          id="chain-auto-escalate-delay"
          className="input"
          type="number"
          min={0}
          value={node.auto_escalate_delay_s ?? 0}
          onChange={(e) => onChange({ auto_escalate_delay_s: Number(e.target.value) })}
        />
      </div>
    </div>
  );
}

/** The selected node's YAML beside its form (D.3): editable, "edits either
 *  side". A keystroke shows at once; a debounced server parse of just this
 *  node replaces it in the draft once the text parses to exactly one node.
 *  Keyed by the node, so switching nodes drops an unparsed draft. */
function NodeYaml({
  tplId,
  node,
  onReplace,
}: {
  tplId: string;
  node: TemplateNode;
  onReplace: (next: TemplateNode) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onChange = (text: string) => {
    setDraft(text);
    if (timer.current) clearTimeout(timer.current);
    const mine = ++seq.current;
    timer.current = setTimeout(() => {
      api
        .parseTemplateYaml(`id: ${tplId}\nnodes:\n${text}`)
        .then((r) => {
          if (mine !== seq.current) return; // a newer keystroke superseded this
          if (r.error || !r.nodes || r.nodes.length !== 1) {
            setError(r.error ?? "the fragment has to describe exactly one node");
            return;
          }
          setError(null);
          setDraft(null);
          onReplace(r.nodes[0]);
        })
        .catch((e) => {
          if (mine === seq.current) setError(e instanceof Error ? e.message : String(e));
        });
    }, 400);
  };

  return (
    <div className="chain-fragment">
      <span className="field-hint">YAML · edits either side</span>
      <textarea
        aria-label="node yaml"
        className="input mono chain-fragment-yaml"
        spellCheck={false}
        value={draft ?? serializeFragment(node)}
        onChange={(e) => onChange(e.target.value)}
      />
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}

/** A tiny load-once hook local to this file — `useResource` re-fetches on
 *  every render of its caller since `load` isn't memoized there, which is
 *  fine at page scope but wrong inside `NodeForm` (re-mounted per selection).
 *  This variant loads once per mount and exposes just the value. */
function useResourceValue<T>(load: () => Promise<T>): [T | null] {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    load()
      .then((v) => alive && setValue(v))
      .catch(() => {});
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return [value];
}

export function TemplatesPage() {
  const { value, reload } = useResource(() => api.getTemplates());
  const { value: policy } = useResource(() => api.getPolicy());
  const templates = useMemo(() => value ?? [], [value]);
  const usedBy = useUsedByCounts(templates);
  const phone = usePhone();
  const [params, setParams] = useSearchParams();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const tplId = params.get("tpl") ?? templates[0]?.id ?? null;
  const current = templates.find((t) => t.id === tplId) ?? null;
  const nodeId = params.get("node");

  const [draftNodes, setDraftNodes] = useState<TemplateNode[] | null>(null);
  // Raw text of the full-file YAML while it doesn't yet parse cleanly (or a
  // parse is in flight) — kept apart from `draftNodes` so a keystroke is
  // never reverted by an async round trip. `null` once nodes are the source
  // of truth again (parse landed, or the node form/tab/template changed).
  const [yamlDraftText, setYamlDraftText] = useState<string | null>(null);
  const yamlParseSeq = useRef(0);
  const yamlParseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [tab, setTab] = useState<"yaml" | "diff">("yaml");
  // The YAML toggle swaps the editor card for the whole file (D.4, W7.4).
  const [view, setView] = useState<"form" | "yaml">("form");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [validation, setValidation] = useState<TemplateValidation | null>(null);

  // Reset the draft when the selected template changes underneath it.
  useEffect(() => {
    setDraftNodes(null);
    setYamlDraftText(null);
    setYamlError(null);
    setMessage(null);
  }, [tplId]);

  useEffect(() => () => {
    if (yamlParseTimer.current) clearTimeout(yamlParseTimer.current);
  }, []);

  const nodes = draftNodes ?? current?.nodes ?? [];
  const dirty = draftNodes !== null;
  const selectedIndex = nodes.findIndex((n) => n.id === nodeId);
  const selectedNode = selectedIndex >= 0 ? nodes[selectedIndex] : null;

  // Debounced validation against the registry, same 400ms cadence the YAML
  // parse below uses.
  useEffect(() => {
    if (!current) return;
    const t = setTimeout(() => {
      api
        .validateTemplate(current.id, nodes)
        .then((r) => setValidation(r))
        .catch(() => {});
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, JSON.stringify(nodes)]);

  const yamlText = yamlDraftText ?? (current ? serializeNodes(current.id, nodes) : "");

  const onYamlChange = (text: string) => {
    if (!current) return;
    // Show exactly what was typed, immediately — parsing is async and must
    // not gate what the textarea displays, or every keystroke that passes
    // through invalid YAML gets reverted and the caret jumps to the end.
    setYamlDraftText(text);
    if (yamlParseTimer.current) clearTimeout(yamlParseTimer.current);
    const seq = ++yamlParseSeq.current;
    yamlParseTimer.current = setTimeout(() => {
      api
        .parseTemplateYaml(text)
        .then((r) => {
          if (seq !== yamlParseSeq.current) return; // a newer keystroke already superseded this
          if (r.error || !r.nodes) {
            setYamlError(r.error);
            return;
          }
          setYamlError(null);
          setDraftNodes(r.nodes);
          setYamlDraftText(null);
        })
        .catch((e) => {
          if (seq !== yamlParseSeq.current) return;
          setYamlError(e instanceof Error ? e.message : String(e));
        });
    }, 400);
  };

  const replaceNode = (next: TemplateNode) => {
    if (selectedIndex < 0) return;
    const all = [...nodes];
    all[selectedIndex] = next;
    setDraftNodes(all);
    setYamlDraftText(null); // the node wins over any unparsed full-file text
    // `?node=` selects by id, so renaming a node has to carry the URL along
    // with it — otherwise the param points at an id that no longer exists and
    // the card drops back to the summary.
    if (next.id !== nodeId) setParams({ tpl: current?.id ?? "", node: next.id });
  };

  const patchNode = (patch: Partial<TemplateNode>) => {
    if (selectedNode) replaceNode({ ...selectedNode, ...patch });
  };

  const insertNode = (index: number) => {
    const next = [...nodes];
    const blank: TemplateNode = { id: `node_${next.length + 1}`, tasks: [], gate_after: null };
    next.splice(index, 0, blank);
    setDraftNodes(next);
    setYamlDraftText(null);
    // Selected and shown: the new node's form is what the operator fills next.
    setView("form");
    setParams({ tpl: current?.id ?? "", node: blank.id });
  };

  const duplicateNode = () => {
    if (!selectedNode) return;
    let id = `${selectedNode.id}_copy`;
    for (let k = 2; nodes.some((n) => n.id === id); k++) id = `${selectedNode.id}_copy${k}`;
    const next = [...nodes];
    next.splice(selectedIndex + 1, 0, { ...selectedNode, id });
    setDraftNodes(next);
    setYamlDraftText(null);
    setParams({ tpl: current?.id ?? "", node: id });
  };

  const removeNode = () => {
    if (selectedIndex < 0) return;
    const next = nodes.filter((_, i) => i !== selectedIndex);
    setDraftNodes(next);
    setYamlDraftText(null);
    const prev = next[Math.max(0, selectedIndex - 1)];
    setParams({ tpl: current?.id ?? "", node: prev?.id ?? "" });
  };

  const moveNode = (index: number, dir: -1 | 1) => {
    const j = index + dir;
    if (j < 0 || j >= nodes.length) return;
    const next = [...nodes];
    [next[index], next[j]] = [next[j], next[index]];
    setDraftNodes(next);
    setYamlDraftText(null);
  };

  const save = async () => {
    if (!current || !draftNodes) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTemplate(current.id, draftNodes);
      setDraftNodes(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Writes a template under a new, prompted name and opens it. */
  const saveAs = async (ask: string, nodesFor: (from: TemplateSummary) => TemplateNode[], suggested = "") => {
    const name = window.prompt(ask, suggested);
    if (!name || !current) return;
    if (templates.some((t) => t.id === name)) {
      setMessage(`"${name}" already exists`);
      return;
    }
    try {
      await api.putTemplate(name, nodesFor(current));
      await reload();
      setParams({ tpl: name });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };
  const newTemplate = () => saveAs("New template name", () => [{ id: "node_1", tasks: [], gate_after: null }]);
  const duplicateTemplate = () => saveAs("Duplicate as", (from) => from.nodes, current ? `${current.id}-copy` : "");

  // Scroll the full-file YAML textarea to the selected node's block, the
  // "highlight" this settles for over a rendered overlay a textarea cannot do.
  useEffect(() => {
    if (!textareaRef.current || selectedIndex < 0) return;
    const before = serializeNodes(current?.id ?? "", nodes.slice(0, selectedIndex));
    const line = before.split("\n").length;
    const lineHeight = 18;
    textareaRef.current.scrollTop = Math.max(0, (line - 2) * lineHeight);
  }, [selectedIndex, current, nodes, view]);

  if (!current) {
    return (
      <div className="chain-head">
        <h2 className="chain-head-name">Chains</h2>
        <span className="chain-head-counts">no chain templates yet</span>
      </div>
    );
  }

  const loopNames = policy ? Object.keys(policy.loops) : [];
  const earlierIds = selectedIndex > 0 ? nodes.slice(0, selectedIndex).map((n) => n.id) : [];

  // D.1: the template dropdown replaces the templates column -- every
  // template, then what you can do with them. Delete has no API route yet.
  const templateItems: OverflowItem[] = [
    ...templates.map((t) => ({
      label: t.id,
      icon: <Check size={14} style={{ visibility: t.id === current.id ? "visible" : "hidden" }} aria-hidden />,
      onSelect: () => setParams({ tpl: t.id }),
    })),
    { label: "New…", divider: true, onSelect: newTemplate },
    { label: "Duplicate", onSelect: duplicateTemplate },
    { label: "Delete", disabled: true, hint: "no delete route for templates yet (Kraft-lwtco)", onSelect: () => {} },
  ];

  const problems =
    validation && !validation.valid ? (
      <div className="validation" data-valid={false}>
        {validation.unresolved?.length ? (
          // Per node and per task, not per repo (settings.py's own comment on
          // why: a repo-level "unresolvable" bit told the operator nothing the
          // node id doesn't already say better).
          validation.unresolved.map((u) => (
            <span key={`${u.node}-${u.task}`}>
              {u.node}: {u.task} has no plugin bound
            </span>
          ))
        ) : (
          <span>{validation.error}</span>
        )}
      </div>
    ) : null;

  const head = (
    <div className="chain-head">
      <h2 className="chain-head-name">{current.id}</h2>
      {validation?.valid && <span className="tag tag-accent validation-badge">valid</span>}
      <span className="chain-head-counts">
        {plural(nodes.length, "node")} · {plural(gatesOf(nodes), "gate")}
      </span>
      <span className="chain-head-spacer" />
      {!phone && <OverflowMenu label="template" text items={templateItems} />}
      <button
        className="btn btn-secondary"
        aria-pressed={view === "yaml"}
        onClick={() => setView((v) => (v === "yaml" ? "form" : "yaml"))}
      >
        YAML
      </button>
      <button className="btn btn-ghost" disabled={!dirty} onClick={() => setDraftNodes(null)}>
        Revert
      </button>
      <button className="btn btn-primary" disabled={busy || !dirty || validation?.valid === false} onClick={save}>
        <Check size={14} />
        Save
      </button>
      {message && <span className="save-hint">{message}</span>}
    </div>
  );

  const legend = (
    <p className="chain-legend">
      <Flag size={11} weight="fill" /> gate after · <ArrowsClockwise size={11} /> fix loop ·{" "}
      <Shield size={11} /> auto-escalate · drag to reorder · ⊕ inserts
    </p>
  );

  const editor =
    view === "yaml" ? (
      <div className="chain-yaml-pane">
        <div className="field-hint">{current.id}.yaml live · edits either side</div>
        <Tabs
          tabs={[
            { id: "yaml", label: "yaml" },
            { id: "diff", label: "diff vs saved" },
          ]}
          value={tab}
          onChange={(id) => setTab(id as "yaml" | "diff")}
        />
        {tab === "yaml" ? (
          <>
            <textarea
              ref={textareaRef}
              aria-label="chain yaml"
              className="input mono template-yaml"
              value={yamlText}
              onChange={(e) => onYamlChange(e.target.value)}
            />
            {yamlError && <p className="form-error">{yamlError}</p>}
          </>
        ) : (
          <DraftDiff before={serializeNodes(current.id, current.nodes)} after={serializeNodes(current.id, nodes)} />
        )}
      </div>
    ) : (
      // D.3: one card -- the node's form and its YAML side by side, or, with
      // nothing selected, what the template is (D.5).
      <div className="card chain-card" data-testid="chain-card">
        {selectedNode ? (
          <div className="chain-card-body">
            <NodeForm
              key={selectedNode.id}
              node={selectedNode}
              index={selectedIndex}
              total={nodes.length}
              loopNames={loopNames}
              earlierIds={earlierIds}
              onChange={patchNode}
            />
            <NodeYaml key={`yaml:${selectedNode.id}`} tplId={current.id} node={selectedNode} onReplace={replaceNode} />
          </div>
        ) : (
          <div className="chain-summary" data-testid="chain-summary">
            <p className="chain-summary-line">
              {plural(nodes.length, "node")} · {plural(gatesOf(nodes), "gate")} · used by{" "}
              {plural(usedBy[current.id] ?? 0, "item")} · <code>~/.kraft/templates/{current.id}.yaml</code>
            </p>
            <p className="field-hint">Select a node to edit it, or ⊕ to insert one.</p>
          </div>
        )}
        <div className="chain-card-foot">
          {selectedNode && (
            <>
              <button type="button" className="btn btn-ghost" aria-label="duplicate node" onClick={duplicateNode}>
                duplicate
              </button>
              <button type="button" className="btn btn-ghost btn-danger" aria-label="remove node" onClick={removeNode}>
                remove
              </button>
            </>
          )}
          {legend}
        </div>
      </div>
    );

  return (
    <>
      {phone && (
        <PhoneHeader
          back="Settings"
          backTo="/settings"
          title="Chains"
          subtitle="~/.kraft/templates"
          action={
            <button className="btn btn-primary" aria-label="New template" onClick={newTemplate}>
              +
            </button>
          }
        />
      )}
      <div className="chain-page">
        {/* D.6: on a phone the dropdown is a full-width native select. */}
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
        {/* Full width above the card (screen 27): the pills stay as built. */}
        <div className="chain-graph-row">
          <NodeGraph
            nodes={nodes}
            selected={nodeId}
            onSelect={(id) => setParams({ tpl: current.id, node: id })}
            onInsert={insertNode}
            onMove={moveNode}
          />
        </div>
        {editor}
        {problems}
      </div>
    </>
  );
}
