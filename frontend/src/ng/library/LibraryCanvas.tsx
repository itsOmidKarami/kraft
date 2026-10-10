import { useContext, useEffect, useRef, useState } from "react";
import { NodeGlyph } from "../graph/NodeGlyph";
import { detailOf } from "../http";
import { isTextField } from "../keys";
import { showToast } from "../ui/Toast";
import { BottomPane, handlerOf, type BottomTab } from "../templates/BottomPane";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { authoredAt, normalise, valueAt } from "../templates/draft/view";
import { NodeView } from "../templates/NodeView";
import type { Row } from "./rows";
import { SteeringCanvas } from "./steering";
import type { Use } from "./types";
import { libSel } from "./sel";
import { ReadOnly } from "../templates/plugin";

/** The Library's canvas (Decisions §10): a node as its steps and tasks, with its failure handlers in the bottom
 *  pane; a step as one step; a task as its one glyph. Drawn from the draft as written (R18). The page keys it by
 *  component, so a new component starts the canvas over. */
export function LibraryCanvas({ draft, row, uses, path, reserve, height, onPick, onOpen, onEscape, onBackground, onGoTo }: {
  draft: ConfigDraft;
  row: Row;
  /** The published uses of the component, for a steering profile's preview; null until the library has loaded. */
  uses: Use[] | null;
  /** The selected path: the component's own, or a part of it. */
  path: string;
  /** Px the side pane covers on the right. */
  reserve: number;
  height: number;
  onPick: (path: string) => void;
  onOpen: (path: string) => void;
  onEscape: () => void;
  onBackground: () => void;
  /** Opens another component of the library (a node's base, a task's base). */
  onGoTo: (id: string) => void;
}) {
  const r = draft.view!.result;
  const scope = draft.scope;
  const readOnly = useContext(ReadOnly);
  const [tab, setTab] = useState<BottomTab>("on_failure");
  const [bottom, setBottom] = useState(false);
  // The bottom pane opens when what is picked lives in it (Decisions §9 Bottom pane).
  const handler = handlerOf(path);
  useEffect(() => {
    if (!handler) return;
    setTab(handler);
    setBottom(true);
  }, [handler]);

  // ⌥←/⌥→ moves the selected step of a node one place, checked first with ?preview=1: a refusal says why and saves nothing.
  const moveSel = useRef<(dir: -1 | 1) => void>(() => {});
  moveSel.current = async (dir) => {
    const sel = libSel(path);
    if (readOnly || row.section !== "nodes" || sel.kind !== "step" || handler) return;
    const ids = (normalise(authoredAt(r, scope, row.id))?.steps ?? []).map((x) => x.id);
    const at = ids.indexOf(sel.step);
    const to = at + dir;
    if (at < 0 || to < 0 || to >= ids.length) return;
    const op = { op: "move", path, to };
    const check = await draft.ops([op], { preview: true });
    if (check.status !== 200) return showToast(`Can't move ${sel.step}: ${detailOf(check.body)}`);
    await draft.ops([op]);
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!e.altKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight") || isTextField(e.target)) return;
      e.preventDefault();
      moveSel.current(e.key === "ArrowLeft" ? -1 : 1);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  if (row.section === "nodes") {
    const own = authoredAt(r, scope, row.id);
    const exec = !!own && own.kind !== "gate";
    return (
      <div className={`tpl-area${exec ? " has-bottom" : ""}`}>
        <NodeView
          scope={scope}
          node={row.id}
          draft={draft}
          selected={libSel(path)}
          reserve={reserve}
          onPick={onPick}
          onOpen={onOpen}
          onEscape={onEscape}
          onBackground={onBackground}
          onBack={onBackground}
          onFocusNode={(id) => onGoTo(`nodes.${id}`)}
        />
        {exec && (
          <BottomPane
            scope={scope}
            node={row.id}
            draft={draft}
            selPath={path}
            tab={tab}
            open={bottom}
            canvasH={height}
            right={reserve}
            onTab={setTab}
            onToggle={() => setBottom((o) => !o)}
            onPick={onPick}
            onOpen={onOpen}
            onLeave={onBackground}
          />
        )}
      </div>
    );
  }
  if (row.section === "steps")
    return (
      <div className="tpl-area">
        <NodeView scope={scope} node="steps" libStep={row.name} draft={draft} selected={libSel(path)} reserve={reserve} onPick={onPick} onOpen={onOpen} onEscape={onEscape} onBackground={onBackground} onBack={onBackground} onFocusNode={(id) => onGoTo(`nodes.${id}`)} />
      </div>
    );
  if (row.section === "tasks") {
    const kind = String(valueAt(r, scope, row.id, "kind") ?? "");
    const base = authoredAt(r, scope, row.id)?.extends;
    return (
      <div className="lib-glyph-area" style={{ right: reserve }}>
        <button type="button" className="lib-glyph" aria-label={`${row.name}, ${kind || "task"} task`} onClick={() => onOpen(row.id)}>
          <NodeGlyph size="lg" icon={row.glyph.icon} taskKind={row.glyph.taskKind} sel={path === row.id} prob={row.problem} mark={row.mark} />
          <span className="lib-glyph-name">{row.name}</span>
        </button>
        <span className="lib-glyph-sub">{kind || "task"}</span>
        {typeof base === "string" && <button type="button" className="tpl-phrase" onClick={() => onGoTo(`tasks.${base}`)}>extends {base}</button>}
      </div>
    );
  }
  return <SteeringCanvas row={row} uses={uses} reserve={reserve} />;
}
