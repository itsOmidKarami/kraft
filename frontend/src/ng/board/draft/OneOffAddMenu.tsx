import { useEffect, useRef, useState, type RefObject } from "react";
import { Bot, Layers } from "lucide-react";
import { Button } from "../../ui/Button";
import { Field } from "../../ui/Field";
import { Select } from "../../ui/Select";
import { sendOnModEnter } from "../../keys";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { Popover } from "../../ui/Popover";
import { ExtendMenu } from "../../templates/menus/ExtendMenu";
import { IdRow } from "../../templates/menus/IdRow";
import { focusSoon } from "../../templates/menus/focus";
import { idError } from "../../templates/ids";
import type { Op } from "../../templates/draft/types";

type Pick = "menu" | "library" | "custom";

/** The menu behind a `+` seam while an item runs a one-off chain of its own: a
 *  library node, or a custom agent node written here. Anything else (removing a
 *  node, an override, another task kind) is the YAML tab's. */
export function OneOffAddMenu({ anchor, at, taken, refused, send, onClose }: {
  anchor: RefObject<HTMLElement | null>;
  at: number;
  taken: string[];
  refused?: string | null;
  /** True when the server applied the ops; the menu closes on it. */
  send: (ops: Op[]) => Promise<boolean>;
  onClose: () => void;
}) {
  const [pick, setPick] = useState<Pick>("menu");
  const [base, setBase] = useState<string | null>(null);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (pick === "menu") focusSoon(first.current); }, [pick]);
  const done = async (ops: Op[]) => { if (await send(ops)) onClose(); };

  if (pick === "library" && !base) return <ExtendMenu anchor={anchor} title="Add a library node" note="It joins this item's own chain." onPick={setBase} onClose={onClose} />;
  if (pick === "library" && base) {
    return (
      <Popover anchor={anchor} open onClose={onClose} role="dialog" label="New node">
        <div className="seam-id">
          <IdRow label="Node id" initial={base.split(":").pop()!} taken={taken} placeholder="e.g. security_scan" go="Create & open →" refused={refused}
            onGo={(id) => void done([{ op: "add_node", at, id, kind: "exec" }, { op: "extend", node: id, base }])} />
        </div>
      </Popover>
    );
  }
  if (pick === "custom") return <CustomAgent anchor={anchor} taken={taken} refused={refused} onClose={onClose} onAdd={(id, harness, prompt) => done([
    { op: "add_node", at, id, kind: "exec" },
    { op: "add_step", container: id, at: 0, id: "run" },
    { op: "add_task", container: id, step: "run", id: "agent", kind: "agent" },
    { op: "set_field", path: `${id}.run.agent`, field: "harness", value: harness },
    { op: "set_field", path: `${id}.run.agent`, field: "prompt", value: prompt },
  ])} />;
  return (
    <Popover anchor={anchor} open onClose={onClose} role="menu" label="Add here">
      <div className="menu seam-menu">
        <button ref={first} type="button" role="menuitem" className="menu-item seam-item" onClick={() => setPick("library")}>
          <Layers size={14} aria-hidden /><span><span className="seam-item-name">From the library</span><span className="seam-item-sub">no review, unless after the final approval</span></span>
        </button>
        <button type="button" role="menuitem" className="menu-item seam-item" onClick={() => setPick("custom")}>
          <Bot size={14} aria-hidden /><span><span className="seam-item-name">Custom agent node</span><span className="seam-item-sub">your own prompt, read before it runs</span></span>
        </button>
      </div>
    </Popover>
  );
}

function CustomAgent({ anchor, taken, refused, onAdd, onClose }: { anchor: RefObject<HTMLElement | null>; taken: string[]; refused?: string | null; onAdd: (id: string, harness: string, prompt: string) => void; onClose: () => void }) {
  const [id, setId] = useState("");
  const opts = useHarnessOptions();
  const [harness, setHarness] = useState("claude");
  // The profiles harnesses.yaml declares; `claude` stands in until they are read, and when they cannot be.
  const profiles = typeof opts === "string" ? [] : opts.harnesses.profiles.map((p) => p.id);
  const names = profiles.includes(harness) || !profiles.length ? profiles : [harness, ...profiles];
  const [prompt, setPrompt] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const err = idError(id, taken) ?? (refused && sent === id ? refused : null);
  const can = !!id && !err && !!harness.trim() && !!prompt.trim();
  const add = () => { setSent(id); onAdd(id, harness.trim(), prompt); };
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label="New custom agent node" dirty={!!(id || prompt.trim())}>
      <form className="oneoff-agent" onSubmit={(e) => { e.preventDefault(); if (can) add(); }}>
        <Field label="Node id" error={err}><input className="item-input" autoFocus spellCheck={false} value={id} placeholder="e.g. load_test" onChange={(e) => setId(e.target.value.trim())} /></Field>
        <Field label="Harness">{names.length ? <Select mono value={harness} onChange={(e) => setHarness(e.target.value)}>{names.map((n) => <option key={n} value={n}>{n}</option>)}</Select> : <input className="item-input" value={harness} onChange={(e) => setHarness(e.target.value)} />}</Field>
        <Field label="Prompt"><textarea className="item-input" rows={5} onKeyDown={sendOnModEnter(add, can)} value={prompt} placeholder="What this agent does. A person reads it before the item starts." onChange={(e) => setPrompt(e.target.value)} /></Field>
        <div className="item-actions">
          <Button variant="primary" type="submit" disabled={!can}>Create &amp; open →</Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Popover>
  );
}
