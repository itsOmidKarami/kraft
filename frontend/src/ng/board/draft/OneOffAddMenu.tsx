import { useEffect, useRef, useState, type RefObject } from "react";
import { Bot, Layers } from "lucide-react";
import { Button } from "../../ui/Button";
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
          <IdRow label="Node id" initial={base.split(":").pop()!} taken={taken} placeholder="e.g. security_scan" go="Add node →" refused={refused}
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
          <Layers size={14} aria-hidden /><span><span className="seam-item-name">From the library</span><span className="seam-item-sub">a node others already review</span></span>
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
  const [harness, setHarness] = useState("claude");
  const [prompt, setPrompt] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const err = idError(id, taken) ?? (refused && sent === id ? refused : null);
  const can = !!id && !err && !!harness.trim() && !!prompt.trim();
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label="New custom agent node">
      <form className="seam-id oneoff-agent" onSubmit={(e) => { e.preventDefault(); if (can) { setSent(id); onAdd(id, harness.trim(), prompt); } }}>
        <label className="oneoff-agent-f"><span>Node id</span><input aria-label="Node id" className="draft-input" autoFocus value={id} placeholder="e.g. load_test" onChange={(e) => setId(e.target.value)} /></label>
        <label className="oneoff-agent-f"><span>Harness</span><input aria-label="Harness" className="draft-input" value={harness} onChange={(e) => setHarness(e.target.value)} /></label>
        <label className="oneoff-agent-f"><span>Prompt</span><textarea aria-label="Prompt" className="draft-input" rows={5} value={prompt} placeholder="What this agent does. A person reads it before the item starts." onChange={(e) => setPrompt(e.target.value)} /></label>
        {err && <p className="idrow-err" role="alert">{err}</p>}
        <Button variant="primary" type="submit" disabled={!can}>Add node →</Button>
      </form>
    </Popover>
  );
}
