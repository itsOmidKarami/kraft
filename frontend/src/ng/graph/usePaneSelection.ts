import { useReducer } from "react";

/** What the side pane shows. Something is always selected (Decisions §9). */
export type Sel =
  | { kind: "chain" }
  | { kind: "node"; node: string }
  | { kind: "step"; node: string; step: string }
  | { kind: "task"; node: string; step: string; task: string };

export type PaneState = {
  level: "chain" | "node";
  /** The node whose view is open, at the node level. */
  node?: string;
  sel: Sel;
  open: boolean;
  /** The person collapsed the pane: picks keep it collapsed until they expand it. */
  userCollapsed: boolean;
};

export type PaneAction =
  | { type: "pick"; sel: Sel }
  | { type: "expand"; sel?: Sel }
  | { type: "collapse" }
  | { type: "background" }
  | { type: "focus"; node: string; sel?: Sel }
  | { type: "back" }
  | { type: "removed" }
  | { type: "escape" };

/** The floor: the chain on the chain canvas, the node in a node view. */
export const floor = (s: PaneState): Sel => (s.level === "node" && s.node ? { kind: "node", node: s.node } : { kind: "chain" });
const same = (a: Sel, b: Sel) => JSON.stringify(a) === JSON.stringify(b);

/** Decisions §9 "Selection and panes", one transition per action. */
export function paneReducer(s: PaneState, a: PaneAction): PaneState {
  switch (a.type) {
    case "pick":
      // Picking what is already selected, while collapsed, expands it.
      if (same(a.sel, s.sel)) return s.open ? s : { ...s, open: true, userCollapsed: false };
      return { ...s, sel: a.sel, open: !s.userCollapsed };
    case "expand":
      return { ...s, sel: a.sel ?? s.sel, open: true, userCollapsed: false };
    case "collapse":
      return { ...s, open: false, userCollapsed: true };
    case "background":
    case "removed":
      // Down to the floor, collapsed to its rail; the person didn't collapse it.
      return { ...s, sel: floor(s), open: false };
    case "focus":
      return { ...s, level: "node", node: a.node, sel: a.sel ?? { kind: "node", node: a.node } };
    case "back":
      return { ...s, level: "chain", node: undefined, sel: { kind: "chain" }, open: false };
    case "escape":
      if (s.open) return paneReducer(s, { type: "collapse" });
      return s.level === "node" ? paneReducer(s, { type: "back" }) : s;
  }
}

export const initialPane = (open: boolean): PaneState => ({ level: "chain", sel: { kind: "chain" }, open, userCollapsed: false });

export function usePaneSelection(open = false) {
  return useReducer(paneReducer, open, initialPane);
}
