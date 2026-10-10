import { useEffect, useMemo, useRef, useState } from "react";
import { isTextField } from "../keys";
import type { ReviewOutcome } from "../../types";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { ItemHeader } from "../item/header/ItemHeader";
import { placeUrl } from "../item/url";
import { openBudgetEditor } from "../item/Workspace";
import { openLimitEditor } from "../item/RaiseLimit";
import { useItem, type ItemDetail } from "../item/useItem";
import { materialized, producerOf } from "../item/chainValues";
import { useOverlay } from "../graph/useResizable";
import { Button } from "../ui/Button";
import { useComments } from "./Comments";
import { DiffView, pickOf, type Pick } from "./DiffView";
import { FileTree } from "./FileTree";
import { GateReview } from "./GateReview";
import { BottomBar, FinishDialog, useSubmit } from "./FinishReview";
import { byNodes, folders, unresolved } from "./model";
import { parsePatch } from "./patch";
import { useDiffPrefs } from "./prefs";
import { Toolbar } from "./Toolbar";
import { useReviewPlace } from "./url";
import { useArtifact, useCompare, useExpanded, useThreads, useViewed } from "./useReview";
// The item header's styles live with it; this page can be the first one loaded.
import "../item/item.css";
import "./review.css";

/** `/work-items/:id/review`: the changes of one item, its threads, and the
 *  review that sends them (W8, spec §6.4). */
export function ReviewPage() {
  const { id = "" } = useParams();
  const loaded = useItem(id);
  const setPageItem = usePageItem((s) => s.set);
  const item = loaded.state === "ready" ? loaded.item : null;
  useEffect(() => {
    setPageItem(item);
    return () => setPageItem(null);
  }, [item, setPageItem]);
  if (loaded.state === "loading") return <div className="review-page" aria-busy="true" />;
  if (loaded.state === "missing")
    return <Placeholder label="Work item not found" note={`There is no work item ${id}. It may have been removed.`} />;
  return <Review item={loaded.item} reload={loaded.reload} />;
}

function Review({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const ended = item.display_status === "done" || item.display_status === "cancelled" || item.display_status === "archived";
  const navigate = useNavigate();
  const toItem = (p: Parameters<typeof placeUrl>[1]) => navigate(placeUrl(item.id, p));
  const [place, setPlace] = useReviewPlace(item);
  const diff = useDiffPrefs();
  const compare = useCompare(item.id, place.from, place.to, !diff.prefs.show_whitespace, item.head_sha);
  const threads = useThreads(item.id);
  const overlay = useOverlay();
  // Under 1024 the list starts closed and opens over the diff (R7).
  const [treeOpen, setTreeOpen] = useState(!overlay);
  useEffect(() => setTreeOpen(!overlay), [overlay]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const setFold = (path: string, folded: boolean) => setCollapsed((s) => {
    const n = new Set(s);
    if (folded) n.add(path);
    else n.delete(path);
    return n;
  });
  const [picked, setPicked] = useState<Pick | null>(null);
  const viewed = useViewed(item.id, place.to, compare);
  // A file marked viewed folds away, and opens again when the mark is taken off.
  const setViewed = (path: string, v: boolean) => {
    setFold(path, v);
    void viewed.toggle(path, v);
  };
  const all = compare.state === "ready" ? compare.data.files : [];
  const files = byNodes(all, place.nodes);
  const diffText = compare.state === "ready" ? compare.data.diff : "";
  const parsed = useMemo(() => new Map(parsePatch(diffText).map((f) => [f.path, f])), [diffText]);
  const { patch, expand } = useExpanded(item.id, place.from, place.to, !diff.prefs.show_whitespace, parsed);
  const notShown = new Set(compare.state === "ready" && compare.data.truncated ? all.filter((f) => !patch.has(f.path)).map((f) => f.path) : []);
  const threadList = threads.state === "ready" ? threads.data : [];
  // With no file chosen, the tree's first: what one-file mode shows.
  const current = place.file && files.some((f) => f.path === place.file) ? place.file : folders(files)[0]?.files[0]?.path ?? null;
  const comments = useComments({
    itemId: item.id,
    compare: compare.state === "ready" ? compare.data : null,
    files,
    patch,
    threads: threadList,
    reload: threads.reload,
    // Sent or cancelled, the pick goes with it, and the focus goes back to the file's lines.
    onClose: (t) => {
      setPicked(null);
      [...document.querySelectorAll<HTMLElement>(".rv-file")].find((f) => f.dataset.file === t.path)?.querySelector<HTMLElement>(".rv-lines")?.focus({ preventScroll: true });
    },
    // The composer's ×: the pick follows, so the diff shades the new range.
    onRetarget: (t) => t.range && setPicked(pickOf(t.path, { side: t.range.startSide ?? t.range.side, line: t.range.start }, { side: t.range.side, line: t.range.end })),
  });
  const artifact = useArtifact(item);
  const submit = useSubmit(item, place.gate, threadList, threads.reload, artifact?.state === "ready" ? artifact.data.digest : null);
  // Finish your review: closed, or open on an outcome (the bar's Request changes opens it there).
  const [finish, setFinish] = useState<{ outcome?: ReviewOutcome } | null>(null);
  // Escape on the page goes back to the item, as the gate review's × does (GR-1). Whatever is open
  // over it keeps its own: the gate review, a menu, a dialog, a composer, the tree over the diff.
  const back = useRef(() => {});
  back.current = () => toItem({ sel: { kind: "chain" } });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.isComposing || e.defaultPrevented || isTextField(e.target)) return;
      if (e.target instanceof Element && e.target.closest('[role="menu"], [role="dialog"], .popover')) return;
      back.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const select = (file: string) => {
    setPlace({ file });
    if (overlay) setTreeOpen(false);
  };

  return (
    <div className={`review-page${diff.plainCode ? " is-plain-code" : ""}`} onKeyDown={(e) => {
      if (e.key !== "Escape" || !overlay || !treeOpen) return;
      e.preventDefault();
      setTreeOpen(false);
    }}>
      <h1 className="review-visually-hidden">Review changes: {item.title}</h1>
      <ItemHeader
        item={item}
        reload={reload}
        onSettings={() => toItem({ sel: { kind: "chain" }, tab: "config" })}
        onRaise={() => {
          if (item.stop?.limit) return void (openLimitEditor(item.id), toItem({ sel: { kind: "chain" } }));
          if (item.stop?.kind === "budget") openBudgetEditor(item.id);
          toItem({ sel: { kind: "chain" }, tab: "config" });
        }}
        onGate={(gate) => setPlace({ gate, doc: true })}
      />
      <Toolbar
        item={item}
        place={place}
        setPlace={setPlace}
        files={all}
        treeOpen={treeOpen}
        onTree={() => setTreeOpen((o) => !o)}
        onCollapseAll={() => setCollapsed(new Set(all.map((f) => f.path)))}
        onExpandAll={() => setCollapsed(new Set())}
        prefs={diff.prefs}
        setPrefs={diff.set}
      />
      {diff.error && <p className="rv-error rv-bar-error" role="alert">{diff.error}</p>}
      <div className={`rv-body${treeOpen ? " has-tree" : ""}${overlay ? " is-overlay" : ""}`}>
        {treeOpen && (
          <FileTree
            files={files}
            untracked={compare.state === "ready" ? compare.data.untracked : []}
            notShown={notShown}
            threads={threadList}
            selected={current}
            isViewed={viewed.isViewed}
            onSelect={select}
            error={viewed.error}
          />
        )}
        <section className="rv-diff" aria-label="Diff" aria-busy={compare.state === "loading"}>
          {compare.state === "error" && (
            <div className="rv-empty" role="alert">
              <p>{compare.error}</p>
              <Button onClick={() => setPlace({ from: "base", to: "latest" })}>Compare from base</Button>
            </div>
          )}
          {compare.state === "ready" && comments.whole}
          {compare.state === "ready" && (
            <DiffView
              files={files}
              patch={patch}
              prefs={diff.prefs}
              collapsed={collapsed}
              onCollapse={setFold}
              selected={current}
              isViewed={viewed.isViewed}
              onViewed={setViewed}
              threadCount={(path) => threadList.filter((t) => t.file_path === path && unresolved(t)).length}
              picked={picked}
              onPick={setPicked}
              onRange={(p) => {
                setPicked(p);
                comments.retargetTo(p, picked);
              }}
              onCompose={comments.openPick}
              onFileComment={(path) => {
                setFold(path, false);
                comments.openFile(path);
              }}
              after={comments.after}
              commented={comments.commented}
              top={comments.top}
              truncated={compare.data.truncated ? { bytes: compare.data.diff_max_bytes, files: notShown.size } : null}
              readOnly={ended}
              onExpand={(path, gap, how) => void expand(path, gap, how)}
            />
          )}
          {compare.state === "ready" && comments.elsewhere}
        </section>
      </div>
      {place.doc && place.gate && item.pending_gate === place.gate && item.gate_artifact && (
        <GateReview
          item={item}
          gate={place.gate}
          files={files}
          threads={threadList}
          isViewed={viewed.isViewed}
          doc={artifact}
          by={producerOf(materialized(item), place.gate)}
          approve={() => submit("approve", "")}
          onReviewChanges={(file) => setPlace({ doc: false, ...(file && { file }) })}
          onRequestChanges={() => {
            setPlace({ doc: false });
            setFinish({ outcome: "request_changes" });
          }}
          onClose={() => toItem({ sel: { kind: "chain" } })}
        />
      )}
      <BottomBar item={item} gate={place.gate} threads={threadList} readOnly={ended} onFinish={(outcome) => setFinish({ outcome })} submit={submit} />
      {finish && <FinishDialog item={item} gate={place.gate} threads={threadList} initial={finish.outcome} submit={submit} onClose={() => setFinish(null)} />}
    </div>
  );
}
