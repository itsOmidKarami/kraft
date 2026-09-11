import { useEffect, useMemo, useRef, useState } from "react";
import { Flag, Shield, ArrowsClockwise, Check } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { SectionLabel, Tabs } from "../../components/ui";
import { adapterOf } from "../../format";
import { useStore } from "../../store";
import type { TemplateNode, TemplateSummary } from "../../types";
import "./templates.css";
import { PageHead, PhoneHeader, usePhone, useResource } from "./shared";

/* ── 5b chain templates (Chains editor, design 27 / m13) ─────────────────── */

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

function usedByCounts(templates: TemplateSummary[]) {
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
        aria-label="insert node at start"
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
            aria-label={`insert node after ${n.id || i}`}
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
  onRemove,
}: {
  node: TemplateNode;
  index: number;
  total: number;
  loopNames: string[];
  earlierIds: string[];
  onChange: (patch: Partial<TemplateNode>) => void;
  onRemove: () => void;
}) {
  const [registry] = useResourceValue(() => api.getRegistry());
  const hooks = registry?.hooks ?? {};
  const tasks = node.tasks ?? [];

  return (
    <div className="chain-node-form">
      <div className="settings-head">
        <h3>
          {node.id || "(unnamed)"} · node {index + 1} of {total}
        </h3>
        <button type="button" className="btn btn-ghost btn-danger" onClick={onRemove}>
          Remove
        </button>
      </div>
      <div className="field">
        <label htmlFor="chain-node-id">id</label>
        <input
          id="chain-node-id"
          className="input"
          value={node.id}
          onChange={(e) => onChange({ id: e.target.value })}
        />
      </div>

      <SectionLabel>Tasks · hook points, in order</SectionLabel>
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
      <button
        type="button"
        className="btn btn-secondary"
        onClick={() => {
          const options = Object.keys(hooks).filter((h) => !tasks.includes(h));
          const pick = window.prompt(`Add task (one of: ${options.join(", ")})`);
          if (pick && options.includes(pick)) onChange({ tasks: [...tasks, pick] });
        }}
      >
        + Add task
      </button>

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
  const usedBy = usedByCounts(templates);
  const phone = usePhone();
  const [params, setParams] = useSearchParams();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const tplId = params.get("tpl") ?? templates[0]?.id ?? null;
  const current = templates.find((t) => t.id === tplId) ?? null;
  const nodeId = params.get("node");

  const [draftNodes, setDraftNodes] = useState<TemplateNode[] | null>(null);
  // Raw text of the YAML textarea while it doesn't yet parse cleanly (or a
  // parse is in flight) — kept apart from `draftNodes` so a keystroke is
  // never reverted by an async round trip. `null` once nodes are the source
  // of truth again (parse landed, or the node form/tab/template changed).
  const [yamlDraftText, setYamlDraftText] = useState<string | null>(null);
  const yamlParseSeq = useRef(0);
  const yamlParseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [tab, setTab] = useState<"yaml" | "diff">("yaml");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [validation, setValidation] = useState<{ valid: boolean; error: string | null } | null>(
    null,
  );

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
        .then((r) => setValidation({ valid: r.valid, error: r.error }))
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

  const patchNode = (patch: Partial<TemplateNode>) => {
    if (selectedIndex < 0) return;
    const next = [...nodes];
    next[selectedIndex] = { ...next[selectedIndex], ...patch };
    setDraftNodes(next);
    setYamlDraftText(null); // node form wins over any unparsed YAML text
    // `?node=` selects by id, so renaming a node from the form has to carry
    // the URL along with it — otherwise the very next keystroke leaves the
    // param pointing at an id that no longer exists and selectedIndex drops
    // to -1, replacing the form with "select a node".
    if (patch.id !== undefined && patch.id !== nodeId) {
      setParams({ tpl: current?.id ?? "", node: patch.id });
    }
  };

  const insertNode = (index: number) => {
    const next = [...nodes];
    const blank: TemplateNode = { id: `node_${next.length + 1}`, tasks: [], gate_after: null };
    next.splice(index, 0, blank);
    setDraftNodes(next);
    setYamlDraftText(null);
    setParams({ tpl: current?.id ?? "", node: blank.id });
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

  const createTemplate = async () => {
    const name = window.prompt("New template name");
    if (!name || !current) return;
    if (templates.some((t) => t.id === name)) {
      setMessage(`"${name}" already exists`);
      return;
    }
    try {
      await api.putTemplate(name, current.nodes);
      await reload();
      setParams({ tpl: name });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  // Scroll the YAML textarea to the selected node's block, the "highlight"
  // this plan settles for over a rendered overlay a plain textarea cannot do.
  useEffect(() => {
    if (!textareaRef.current || selectedIndex < 0) return;
    const before = serializeNodes(current?.id ?? "", nodes.slice(0, selectedIndex));
    const line = before.split("\n").length;
    const lineHeight = 18;
    textareaRef.current.scrollTop = Math.max(0, (line - 2) * lineHeight);
  }, [selectedIndex, current, nodes]);

  if (!current) {
    return (
      <>
        <PageHead title="Chains" note="no chain templates yet" />
      </>
    );
  }

  const loopNames = policy ? Object.keys(policy.loops) : [];
  const earlierIds = selectedIndex > 0 ? nodes.slice(0, selectedIndex).map((n) => n.id) : [];

  /* ── phone: three drill levels, all this one component ─────────────────── */
  if (phone) {
    if (!params.get("tpl")) {
      return (
        <>
          <PhoneHeader
            back="Settings"
            backTo="/settings"
            title="Chains"
            subtitle="~/.kraft/templates"
            action={
              <button className="btn btn-primary" onClick={createTemplate}>
                +
              </button>
            }
          />
          {templates.map((t) => (
            <button
              key={t.id}
              className="facet-opt"
              onClick={() => setParams({ tpl: t.id })}
            >
              {t.id}
              <span className="facet-count">
                {t.nodes.length} nodes · {gatesOf(t.nodes)} gates · used by {usedBy[t.id] ?? 0}{" "}
                items
              </span>
            </button>
          ))}
        </>
      );
    }
    if (!nodeId) {
      return (
        <>
          <PhoneHeader
            back="Chains"
            backTo="/settings/chains"
            title={current.id}
            subtitle={`${nodes.length} nodes`}
          />
          {nodes.map((n, i) => (
            <div key={`${n.id}-${i}`}>
              <button
                className="facet-opt"
                onClick={() => setParams({ tpl: current.id, node: n.id })}
              >
                {i + 1}. {n.id}
                {n.gate_after && <Flag size={11} weight="fill" />}
                {n.fix_loop && <ArrowsClockwise size={11} />}
                {n.auto_escalate && <Shield size={11} />}
                <span className="facet-count">{(n.tasks ?? []).length} tasks</span>
              </button>
              <button
                type="button"
                className="chain-insert"
                aria-label={`insert node after ${n.id}`}
                onClick={() => insertNode(i + 1)}
              >
                ⊕
              </button>
            </div>
          ))}
        </>
      );
    }
    if (selectedNode) {
      return (
        <>
          <PhoneHeader
            back={current.id}
            backTo={`/settings/chains?tpl=${current.id}`}
            title={selectedNode.id}
            subtitle={`${current.id} · node ${selectedIndex + 1} of ${nodes.length}`}
            action={
              <button className="btn btn-primary" disabled={!dirty} onClick={save}>
                Save
              </button>
            }
          />
          <NodeForm
            node={selectedNode}
            index={selectedIndex}
            total={nodes.length}
            loopNames={loopNames}
            earlierIds={earlierIds}
            onChange={patchNode}
            onRemove={removeNode}
          />
          <SectionLabel>YAML · read-only here — the node form writes it</SectionLabel>
          <textarea
            aria-label="chain yaml"
            className="input mono template-yaml"
            value={yamlText}
            readOnly
          />
        </>
      );
    }
  }

  /* ── desktop ─────────────────────────────────────────────────────────── */
  return (
    <>
      <PageHead
        title={current.id}
        note={`~/.kraft/templates/${current.id}.yaml · ${nodes.length} nodes · ${gatesOf(nodes)} gates`}
        action={
          <div className="save-row">
            <button className="btn btn-primary" disabled={busy || !dirty || validation?.valid === false} onClick={save}>
              <Check size={14} />
              Save
            </button>
            <button className="btn btn-ghost" disabled={!dirty} onClick={() => setDraftNodes(null)}>
              Revert
            </button>
            <span className="save-hint">{message}</span>
          </div>
        }
      />
      <div className="template-editor chain-editor">
        <div className="template-list">
          <SectionLabel>Templates</SectionLabel>
          {templates.map((t) => (
            <button
              key={t.id}
              className="facet-opt"
              aria-pressed={t.id === current.id}
              onClick={() => setParams({ tpl: t.id })}
            >
              {t.id}
              <span className="facet-count">
                {t.nodes.length} nodes · {gatesOf(t.nodes)} gates · used by {usedBy[t.id] ?? 0}{" "}
                items
              </span>
            </button>
          ))}
          <button className="btn btn-secondary" onClick={createTemplate}>
            New
          </button>
          {validation && (
            <div className="validation" data-valid={validation.valid}>
              <span>{validation.valid ? "valid" : validation.error}</span>
            </div>
          )}
        </div>

        <div className="chain-middle">
          <NodeGraph
            nodes={nodes}
            selected={nodeId}
            onSelect={(id) => setParams({ tpl: current.id, node: id })}
            onInsert={insertNode}
            onMove={moveNode}
          />
          {selectedNode ? (
            <NodeForm
              node={selectedNode}
              index={selectedIndex}
              total={nodes.length}
              loopNames={loopNames}
              earlierIds={earlierIds}
              onChange={patchNode}
              onRemove={removeNode}
            />
          ) : (
            <p className="empty">select a node</p>
          )}
        </div>

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
            <DraftDiff
              before={serializeNodes(current.id, current.nodes)}
              after={serializeNodes(current.id, nodes)}
            />
          )}
        </div>
      </div>
    </>
  );
}
