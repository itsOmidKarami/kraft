import { useCallback, useEffect, useState } from "react";
import { Pencil } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { usePaneSelection } from "../graph/usePaneSelection";
import { useResizable, useWidth } from "../graph/useResizable";
import { detailOf } from "../http";
import { isTextField } from "../keys";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { IconButton } from "../ui/IconButton";
import { showToast } from "../ui/Toast";
import { ChainCanvas } from "./ChainCanvas";
import { ChainPane } from "./panes/ChainPane";
import { useConfigDraft, type ConfigDraft } from "./draft/useConfigDraft";
import { authoredNodes, counts, kindOf } from "./draft/view";
import { CHAIN_SEL, pathOf, selOf, type TSel } from "./sel";
import "./templates.css";

export const chainUrl = (chain: string) => `/templates/chains/${encodeURIComponent(chain)}`;
export const nodeUrl = (chain: string, node: string) => `${chainUrl(chain)}/nodes/${encodeURIComponent(node)}`;

/** The Chains editor (Decisions §9): one chain's server-side draft on a canvas.
 *  Keyed by chain, so switching chains starts the page over. */
export function ChainsPage() {
  const { chain = "", node } = useParams();
  return <ChainEditor key={chain} chain={chain} node={node} />;
}

function ChainEditor({ chain, node }: { chain: string; node?: string }) {
  const draft = useConfigDraft("chains", chain);
  if (draft.status === "notFound") return <div className="tpl-note"><h1>No chain called {chain}</h1></div>;
  if (draft.status === "error" && !draft.view) return <div className="tpl-note" role="alert">Couldn't load {chain}. {draft.error}</div>;
  if (!draft.view) return <div className="tpl-note">Loading {chain}…</div>;
  return <Editor chain={chain} node={node} draft={draft} />;
}

function Editor({ chain, node, draft }: { chain: string; node?: string; draft: ConfigDraft }) {
  const navigate = useNavigate();
  const view = draft.view!;
  const r = view.result;
  const [frame, canvasW] = useWidth();
  const size = useResizable("chains", canvasW);
  // The chain's own pane is the floor and open on load (Decisions §9 Chain settings).
  const [s, dispatch] = usePaneSelection(true);
  const [refused, setRefused] = useState<string | null>(null);
  const [nextProblem, setNextProblem] = useState(0);
  const taskPaths = r.resolved?.task_paths;

  // The URL names the node view; the pane's level follows it.
  useEffect(() => {
    if (node && (s.level !== "node" || s.node !== node)) dispatch({ type: "focus", node });
    if (!node && s.level === "node") dispatch({ type: "back" });
    // Only when the URL moves: the page's own moves dispatch before they navigate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node]);

  useEffect(() => {
    if (draft.error) showToast(draft.error);
  }, [draft.error]);

  // ⌘Z undoes the last request, outside a text field (brief Decided 4).
  const undo = draft.undo;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "z" || isTextField(e.target)) return;
      e.preventDefault();
      undo();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [undo]);

  const focusNode = useCallback((id: string, sel?: TSel) => {
    dispatch({ type: "focus", node: id, sel });
    navigate(nodeUrl(chain, id));
  }, [chain, navigate, dispatch]);

  /** Selects a canonical path and opens its pane: a problem, a change, a crumb. */
  const goTo = useCallback((path: string) => {
    const sel = selOf(path, taskPaths);
    if (sel.kind === "chain" || sel.kind === "node") {
      if (s.level === "node") {
        dispatch({ type: "back" });
        navigate(chainUrl(chain));
      }
      dispatch({ type: "expand", sel });
      return;
    }
    focusNode(sel.node, sel);
    dispatch({ type: "expand", sel });
  }, [taskPaths, s.level, dispatch, navigate, chain, focusNode]);

  const onEscape = () => {
    if (!s.open && s.level === "node") {
      dispatch({ type: "back" });
      navigate(chainUrl(chain));
    } else dispatch({ type: "escape" });
  };

  const add = async (at: number, kind: "exec" | "gate", id: string) => {
    const a = await draft.ops([{ op: "add_node", at, id, kind }], { quiet: true });
    if (a.status !== 200) {
      setRefused(detailOf(a.body));
      return false;
    }
    setRefused(null);
    // A new exec node opens in its node view; a new gate on its pane (Decisions §9).
    if (kind === "exec") focusNode(id);
    else dispatch({ type: "expand", sel: selOf(id) });
    return true;
  };

  const n = counts(r);
  const reserve = size.overlay ? 0 : s.open ? size.width : 40;
  const sel = s.sel as TSel;
  const selPath = pathOf(sel);
  const nodes = authoredNodes(r, chain);
  const selNode = sel.kind === "chain" ? null : nodes.find((x) => x.id === sel.node);
  const isGate = !!selNode && sel.kind === "node" && kindOf(r, selNode) === "gate";

  return (
    <div className="tpl-page" ref={frame}>
      <HeaderTail>
        <span className="tpl-crumb-sep" aria-hidden>›</span>
        <span className="tpl-crumb-chain">{chain}</span>
        {s.level === "node" && s.node && (
          <>
            <span className="tpl-crumb-sep" aria-hidden>›</span>
            <span className="tpl-crumb-node">{s.node}</span>
          </>
        )}
        {view.draft
          ? <span className="tpl-draft">DRAFT · {n.changes} {n.changes === 1 ? "CHANGE" : "CHANGES"}</span>
          : <span className="tpl-published">published</span>}
        {n.problems > 0 && (
          <button
            type="button"
            className="tpl-problems"
            title="Step through the problems"
            onClick={() => {
              const list = r.problems;
              if (!list.length) return;
              goTo(list[nextProblem % list.length].path);
              setNextProblem(nextProblem + 1);
            }}
          >
            {n.problems} {n.problems === 1 ? "PROBLEM" : "PROBLEMS"}
          </button>
        )}
      </HeaderTail>
      <HeaderActions>
        {s.level === "chain" && (
          <IconButton label="Chain settings" onClick={() => dispatch({ type: "expand", sel: CHAIN_SEL })}>
            <Pencil size={14} aria-hidden />
          </IconButton>
        )}
      </HeaderActions>
      <div className="tpl-area">
        {s.level === "chain" ? (
          <ChainCanvas
            chain={chain}
            result={r}
            selected={sel.kind === "node" ? sel.node : undefined}
            pending={draft.pending}
            reserve={reserve}
            refused={refused}
            onSelect={(id) => dispatch({ type: "pick", sel: selOf(id) })}
            onOpen={(id) => dispatch({ type: "expand", sel: selOf(id) })}
            onFocusNode={(id) => focusNode(id)}
            onEscape={onEscape}
            onBackground={() => dispatch({ type: "background" })}
            onAdd={add}
          />
        ) : (
          <div className="tpl-note">{s.node}</div>
        )}
        <ChainPane
          draft={draft}
          chain={chain}
          path={selPath}
          open={s.open}
          size={size}
          onCollapse={() => dispatch({ type: "collapse" })}
          onExpand={() => dispatch({ type: "expand" })}
          onFocus={s.level === "chain" && sel.kind === "node" && !isGate ? () => focusNode(sel.node) : undefined}
          goTo={goTo}
        />
      </div>
    </div>
  );
}
