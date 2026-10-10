import { useCallback, useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { usePaneSelection } from "../graph/usePaneSelection";
import { useResizable } from "../graph/useResizable";
import { detailOf } from "../http";
import { isTextField } from "../keys";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { IconButton } from "../ui/IconButton";
import { showToast } from "../ui/Toast";
import { ChainCanvas } from "./ChainCanvas";
import { BottomPane, handlerOf, type BottomTab } from "./BottomPane";
import { NodeView } from "./NodeView";
import { ChainPane } from "./panes/ChainPane";
import { ReviewPane } from "./ReviewPane";
import { YamlView } from "./YamlView";
import { Switcher, type SwitchTo } from "./Switcher";
import { Dialog } from "../ui/Dialog";
import { draftsChanged, postOps } from "./draft/draftApi";
import * as api from "../../api";
import { Button } from "../ui/Button";
import { useConfigDraft, type ConfigDraft } from "./draft/useConfigDraft";
import { authoredNodes, chainFile, counts, kindOf, liveChainId } from "./draft/view";
import { PluginBadge, ReadOnly } from "./plugin";
import { CHAIN_SEL, pathOf, selOf, type TSel } from "./sel";
import "./templates.css";

/** An element's width and height, kept current. */
export function useBox() {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 600 });
  useEffect(() => {
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight || 600 });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, box.w, box.h] as const;
}

/** How long a click waits to be sure it isn't the first of a double-click (the
 *  common system default; a slower pair still ends the rename it began). */
export const DOUBLE_CLICK_MS = 500;

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
  const scope = draft.scope;
  const r = view.result;
  // A plugin's chain is read here and copied, never edited: every write answers 409.
  const plugin = view.plugin ?? null;
  const readOnly = !!plugin;
  const [frame, canvasW, areaH] = useBox();
  const size = useResizable("chains", canvasW);
  // The chain's own pane is the floor and open on load (Decisions §9 Chain settings).
  const [s, dispatch] = usePaneSelection(true);
  const [refused, setRefused] = useState<string | null>(null);
  const [bottomTab, setBottomTab] = useState<BottomTab>("on_failure");
  const [bottomOpen, setBottomOpen] = useState(false);
  // Review & publish is a mode of the chain canvas (Decisions §9 Publish).
  const [review, setReview] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [highlight, setHighlight] = useState<string | undefined>();
  const [published, setPublished] = useState<{ text: string; nodes: { id: string; kind: "exec" | "gate" }[] } | null | undefined>(undefined);
  // The published library's text: the second file's diff when this draft moved a component into it (R47).
  const [libText, setLibText] = useState<string | null | undefined>(undefined);
  const [nextProblem, setNextProblem] = useState(0);
  // Leaving a chain with a draft asks first (Decisions §9 Unpublished changes, brief Decided 5).
  const [pendingGo, setPendingGo] = useState<SwitchTo | null>(null);
  const [dupTick, setDupTick] = useState(0);
  // The nodes an open remove card lists, marked red on the canvas (Decisions §9 Remove).
  const [marked, setMarked] = useState<string[]>([]);
  const [strip, setStrip] = useState<{ ok: boolean; text: string } | null>(null);
  // A click on the selected node's name renames it once no second click makes it a double-click.
  // The path whose title the pane edits in place, held here so a remount (after
  // Review & publish) starts without it.
  const [renaming, setRenaming] = useState<string | null>(null);
  const renameTimer = useRef<number | undefined>(undefined);
  const stopRenameTimer = () => window.clearTimeout(renameTimer.current);
  useEffect(() => stopRenameTimer, []);
  // The chain's YAML is a second view of the same draft (Decisions §9 YAML).
  const [surface, setSurface] = useState<"canvas" | "yaml">("canvas");
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

  // The bottom pane opens collapsed on each node entry, unless the person came
  // to open something that lives in it (Decisions §9 Bottom pane).
  const selPathNow = pathOf(s.sel as TSel);
  useEffect(() => {
    const h = handlerOf(selPathNow);
    setBottomOpen(!!h);
    if (h) setBottomTab(h);
    // Only on entering a node view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.node]);

  // Another selection ends an in-place rename.
  useEffect(() => {
    setRenaming((p) => (p === selPathNow ? p : null));
  }, [selPathNow]);

  // ⌘Z undoes the last request, outside a text field (brief Decided 4).
  const undo = draft.undo;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (readOnly || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "z" || isTextField(e.target)) return;
      e.preventDefault();
      undo();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [undo, readOnly]);

  // ⌥←/⌥→ moves the selected node, or step in a node view, one place (Decisions §9 Reorder).
  const moveSel = useRef<(dir: -1 | 1) => void>(() => {});
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!e.altKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight") || isTextField(e.target)) return;
      e.preventDefault();
      moveSel.current(e.key === "ArrowLeft" ? -1 : 1);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const focusNode = useCallback((id: string, sel?: TSel) => {
    // A double-click opens the node view: the click before it renames nothing.
    stopRenameTimer();
    setRenaming(null);
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

  /** The published file: the YAML diffs' left side, and where removed nodes stood. */
  const loadPublished = useCallback(() => {
    api.getTemplate(chain).then((f) => {
      const nodes = ((f.chain.nodes as { id: string; kind?: string; extends?: string }[] | undefined) ?? []).map((n) => ({ id: n.id, kind: (n.kind === "gate" ? "gate" : "exec") as "exec" | "gate" }));
      setPublished({ text: f.text, nodes });
    }).catch(() => setPublished(null));
    api.getLibrary().then((l) => setLibText(l.text)).catch(() => setLibText(null));
  }, [chain]);
  const startReview = () => {
    stopRenameTimer();
    setRenaming(null);
    if (s.level === "node") {
      dispatch({ type: "back" });
      navigate(chainUrl(chain));
    }
    setSurface("canvas");
    setReview(true);
    setReviewOpen(true);
    setHighlight(undefined);
    loadPublished();
  };
  const yamlErr = r.yaml_error;
  const toggleYaml = () => {
    // A syntax error keeps you here until it is fixed or reverted (Decisions §9 YAML): the button is disabled.
    if (surface === "yaml") return setSurface("canvas");
    draft.flush();
    loadPublished();
    setSurface("yaml");
  };
  const endReview = () => {
    setReview(false);
    setHighlight(undefined);
  };

  /** Switch to a chain, or start a new one or a copy (`new_chain` on the new key). */
  const perform = async (to: SwitchTo) => {
    setPendingGo(null);
    if (to.kind === "switch") return navigate(chainUrl(to.id));
    const a = await postOps("chains", to.id, [{ op: "new_chain", ...(to.kind === "dup" ? { from: chain } : {}) }]);
    if (a.status !== 200) return showToast(detailOf(a.body));
    draftsChanged();
    showToast(to.kind === "dup" ? `${readOnly ? "Copied to your library" : "Duplicated"} as ${to.id}` : `New chain ${to.id} · add its first node with +`);
    navigate(chainUrl(to.id));
  };
  const requestGo = (to: SwitchTo) => {
    if (to.kind === "switch" && to.id === chain) return;
    if (view.draft) setPendingGo(to);
    else void perform(to);
  };
  const neverPublished = view.base[chainFile(chain)] === null;

  const onEscape = () => {
    stopRenameTimer();
    if (review) return endReview();
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

  /** A move, checked first with ?preview=1: a refusal says why and saves nothing. */
  const move = async (path: string, to: number) => {
    const op = { op: "move", path, to };
    const check = await draft.ops([op], { preview: true });
    if (check.status !== 200) return showToast(`Can't move ${path.split(".").pop()}: ${detailOf(check.body)}`);
    await draft.ops([op]);
  };
  moveSel.current = (dir) => {
    const cur = s.sel as TSel;
    if (readOnly || review || surface !== "canvas") return;
    if (s.level === "chain" && cur.kind === "node") {
      const i = authoredNodes(r, scope).findIndex((x) => x.id === cur.node);
      const to = i + dir;
      if (i >= 0 && to >= 0 && to < authoredNodes(r, scope).length) void move(cur.node, to);
    } else if (s.level === "node" && cur.kind === "step" && s.node) {
      const steps = draft.resolvedNode(s.node)?.steps ?? [];
      const i = steps.findIndex((x) => `${s.node}.${x.id}` === pathOf(cur));
      const to = i + dir;
      if (i >= 0 && to >= 0 && to < steps.length) void move(pathOf(cur), to);
    }
  };

  const n = counts(r);
  // The id a rename in the draft moved the chain to (R10b-02).
  const liveId = liveChainId(r.model, chain);
  const paneOpen = review ? reviewOpen : s.open;
  const reserve = size.overlay ? 0 : paneOpen ? size.width : 40;
  const sel = s.sel as TSel;
  const selPath = pathOf(sel);
  // The selection as it is now, for a timer or a request that ends after a pick.
  const latest = useRef({ selPath, level: s.level, node: s.node });
  latest.current = { selPath, level: s.level, node: s.node };
  const nodes = authoredNodes(r, scope);
  const selNode = sel.kind === "chain" ? null : nodes.find((x) => x.id === sel.node);
  const isGate = !!selNode && sel.kind === "node" && kindOf(r, selNode) === "gate";

  return (
    <ReadOnly.Provider value={readOnly}>
    <div className="tpl-page" ref={frame}>
      <HeaderTail>
        <span className="tpl-crumb-sep" aria-hidden>›</span>
        <Switcher chain={chain} renamedTo={liveId !== chain ? liveId : undefined} onGo={requestGo} startDup={dupTick} copy={readOnly} />
        {plugin && <PluginBadge plugin={plugin} />}
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
        {s.level === "chain" && !review && (
          <Button aria-pressed={surface === "yaml"} disabled={surface === "yaml" && !!yamlErr} title={surface === "yaml" && yamlErr ? `Fix line ${yamlErr.line} first, or revert` : undefined} onClick={toggleYaml}>
            {surface === "yaml" ? "⇄ Canvas" : "YAML"}
          </Button>
        )}
        {!readOnly && s.level === "chain" && !review && surface === "canvas" && (
          <IconButton label="Chain settings" onClick={() => dispatch({ type: "expand", sel: CHAIN_SEL })}>
            <Pencil size={14} aria-hidden />
          </IconButton>
        )}
        {/* With no draft there is nothing to publish: the server would answer 404 (R8b-04), as Repos, Policy and Harnesses already know. */}
        {readOnly
          ? <Button variant="primary" onClick={() => setDupTick((k) => k + 1)}>Copy to my library</Button>
          : <Button variant="primary" aria-pressed={review} disabled={!review && !view.draft} title={!review && !view.draft ? "Nothing to publish: no draft" : undefined} onClick={review ? endReview : startReview}>Review &amp; publish</Button>}
      </HeaderActions>
      <div className={`tpl-area${s.level === "node" ? " has-strip" : ""}${s.level === "node" && selNode && kindOf(r, selNode) === "exec" ? " has-bottom" : ""}`}>
        {s.level === "chain" ? (
          <ChainCanvas
            scope={scope}
            result={r}
            selected={sel.kind === "node" ? sel.node : undefined}
            pending={draft.pending}
            reserve={reserve}
            refused={refused}
            onSelect={(id) => {
              if (review) return setHighlight(id);
              // A click on the selected node's name while its pane is open renames it in the pane
              // (Decisions §9 Rename), unless a second click makes it a double-click, which opens it.
              stopRenameTimer();
              if (sel.kind === "node" && sel.node === id && s.open) {
                // Only if the node is still what the pane shows when it fires.
                renameTimer.current = window.setTimeout(() => latest.current.selPath === id && setRenaming(id), DOUBLE_CLICK_MS);
                return;
              }
              dispatch({ type: "pick", sel: selOf(id) });
            }}
            onOpen={(id) => (review ? setHighlight(id) : dispatch({ type: "expand", sel: selOf(id) }))}
            onFocusNode={(id) => !review && focusNode(id)}
            onEscape={onEscape}
            onBackground={() => {
              stopRenameTimer();
              if (review) setHighlight(undefined);
              else dispatch({ type: "background" });
            }}
            onAdd={add}
            review={review ? { published: published?.nodes ?? [], highlight } : undefined}
            marked={marked}
            strip={strip}
            onDrag={{
              over: async (id, to) => {
                setStrip({ ok: true, text: `Move ${id} here` });
                const a = await draft.ops([{ op: "move", path: id, to }], { preview: true });
                setStrip(a.status === 200 ? { ok: true, text: `Move ${id} here` } : { ok: false, text: `Can't move ${id}: ${detailOf(a.body)}` });
              },
              drop: (id, to) => {
                const i = authoredNodes(r, scope).findIndex((x) => x.id === id);
                if (i !== to) void move(id, to);
              },
              end: () => setStrip(null),
            }}
          />
        ) : (
          <NodeView
            scope={scope}
            node={s.node!}
            draft={draft}
            selected={sel}
            reserve={reserve}
            onPick={(p) => dispatch({ type: "pick", sel: selOf(p, taskPaths) })}
            onOpen={(p) => dispatch({ type: "expand", sel: selOf(p, taskPaths) })}
            onEscape={onEscape}
            onBackground={() => dispatch({ type: "background" })}
            onBack={() => {
              dispatch({ type: "back" });
              navigate(chainUrl(chain));
            }}
            onFocusNode={(id) => focusNode(id)}
          />
        )}
        {s.level === "node" && s.node && selNode && kindOf(r, selNode) === "exec" && (
          <BottomPane
            scope={scope}
            node={s.node}
            draft={draft}
            selPath={selPath}
            tab={bottomTab}
            open={bottomOpen}
            canvasH={areaH - 64}
            right={reserve}
            onTab={setBottomTab}
            onToggle={() => setBottomOpen((o) => !o)}
            onPick={(p) => {
              const h = handlerOf(p);
              if (h) setBottomTab(h);
              dispatch({ type: "pick", sel: selOf(p, taskPaths) });
            }}
            onOpen={(p) => dispatch({ type: "expand", sel: selOf(p, taskPaths) })}
            onLeave={() => dispatch({ type: "background" })}
          />
        )}
        {surface === "yaml" && s.level === "chain" && !review && <YamlView draft={draft} scope={scope} published={published === undefined ? undefined : published?.text ?? null} />}
        {review ? (
          <ReviewPane
            draft={draft}
            scope={scope}
            published={published === undefined ? undefined : published?.text ?? null}
            libraryPublished={libText}
            open={reviewOpen}
            size={size}
            onCollapse={() => setReviewOpen(false)}
            onExpand={() => setReviewOpen(true)}
            onHighlight={(p) => setHighlight(p.split(".")[0])}
            onFix={(p) => {
              endReview();
              goTo(p);
            }}
            onDone={endReview}
            onGone={(to) => navigate(to ? chainUrl(to) : "/templates/chains")}
          />
        ) : (
          <ChainPane
            draft={draft}
            scope={scope}
            path={selPath}
            open={s.open}
            size={size}
            onCollapse={() => dispatch({ type: "collapse" })}
            onExpand={() => dispatch({ type: "expand" })}
            onFocus={s.level === "chain" && sel.kind === "node" && !isGate ? () => focusNode(sel.node) : undefined}
            goTo={goTo}
            onDuplicate={() => setDupTick((k) => k + 1)}
            onDeleted={startReview}
            onMarking={setMarked}
            renaming={renaming}
            onRenaming={setRenaming}
            onRemoved={() => {
              if (s.level === "node" && pathOf(s.sel as TSel) === s.node) {
                // Removing the node you're in returns to the chain's rail (Decisions §9 Removing).
                dispatch({ type: "back" });
                navigate(chainUrl(chain));
              } else dispatch({ type: "removed" });
            }}
            onRenamed={(from, to) => {
              // What is selected once the rename lands: a pick made since (a click
              // elsewhere committed it) wins; a selection in what was renamed follows it.
              const now = latest.current;
              if (!from || (now.selPath !== from && !now.selPath.startsWith(`${from}.`))) return;
              const sel2 = selOf(to + now.selPath.slice(from.length), taskPaths);
              if (now.level === "node" && now.node === from) {
                dispatch({ type: "focus", node: to, sel: sel2 });
                navigate(nodeUrl(chain, to));
              } else dispatch({ type: "pick", sel: sel2 });
            }}
          />
        )}
      </div>
      {pendingGo && (
        <Dialog
          title="You have unpublished changes"
          onClose={() => setPendingGo(null)}
          footer={
            <>
              <Button variant="danger" onClick={async () => {
                const a = await draft.discard();
                if (a.status === 204 || a.status === 404) void perform(pendingGo);
              }}>{neverPublished ? "Discard chain & continue" : "Discard & continue"}</Button>
              <span className="bp-gap" />
              <Button onClick={() => setPendingGo(null)}>Stay</Button>
              {n.problems ? (
                <Button variant="primary" onClick={() => { setPendingGo(null); startReview(); }}>Review problems</Button>
              ) : (
                <Button variant="primary" onClick={async () => {
                  const a = await draft.publish();
                  if (a.status === 200) void perform(pendingGo);
                  else { setPendingGo(null); startReview(); }
                }}>Publish &amp; continue</Button>
              )}
            </>
          }
        >
          <div className="unsaved">
            <p>
              {chain} has {neverPublished ? "never been published" : `${n.changes} unpublished change${n.changes === 1 ? "" : "s"}`}. Publish or discard {neverPublished ? "it" : "them"} before{" "}
              {pendingGo.kind === "switch" ? `switching to ${pendingGo.id}` : pendingGo.kind === "dup" ? `duplicating it as ${pendingGo.id}` : `creating ${pendingGo.id}`}.
            </p>
            {n.problems > 0 && <p>{n.problems} problem{n.problems === 1 ? "" : "s"} block publishing. Review them first, or discard.</p>}
          </div>
        </Dialog>
      )}
    </div>
    </ReadOnly.Provider>
  );
}
