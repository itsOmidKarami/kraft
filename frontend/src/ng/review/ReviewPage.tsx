import { useEffect, useMemo, useState } from "react";
import type { ReviewOutcome } from "../../types";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { ItemHeader } from "../item/header/ItemHeader";
import { placeUrl } from "../item/url";
import { useItem, type ItemDetail } from "../item/useItem";
import { useOverlay } from "../graph/useResizable";
import { Button } from "../ui/Button";
import { useComments } from "./Comments";
import { DiffView, type Pick } from "./DiffView";
import { FileTree } from "./FileTree";
import { GateReview } from "./GateReview";
import { BottomBar, FinishDialog, useSubmit } from "./FinishReview";
import { byNodes, folders, unresolved } from "./model";
import { parsePatch } from "./patch";
import { useDiffPrefs } from "./prefs";
import { Toolbar } from "./Toolbar";
import { useReviewPlace } from "./url";
import { useCompare, useThreads, useViewed } from "./useReview";
// The item header's styles live with it; this page can be the first one loaded.
import "../item/item.css";
import "./review.css";

/** `/ng/work-items/:id/review`: the changes of one item, its threads, and the
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
  const [picked, setPicked] = useState<Pick | null>(null);
  const viewed = useViewed(item.id, place.to, compare);
  const all = compare.state === "ready" ? compare.data.files : [];
  const files = byNodes(all, place.nodes);
  const diffText = compare.state === "ready" ? compare.data.diff : "";
  const patch = useMemo(() => new Map(parsePatch(diffText).map((f) => [f.path, f])), [diffText]);
  const notShown = new Set(compare.state === "ready" && compare.data.truncated ? all.filter((f) => !patch.has(f.path)).map((f) => f.path) : []);
  const threadList = threads.state === "ready" ? threads.data : [];
  // With no file chosen, the tree's first: what one-file mode shows.
  const current = place.file && files.some((f) => f.path === place.file) ? place.file : folders(files)[0]?.files[0]?.path ?? null;
  const comments = useComments({ itemId: item.id, compare: compare.state === "ready" ? compare.data : null, files, patch, threads: threadList, reload: threads.reload });
  const submit = useSubmit(item, place.gate, threadList, threads.reload);
  // Finish your review: closed, or open on an outcome (the bar's Request changes opens it there).
  const [finish, setFinish] = useState<{ outcome?: ReviewOutcome } | null>(null);
  const select = (file: string) => {
    setPlace({ file });
    if (overlay) setTreeOpen(false);
  };

  return (
    <div className={`review-page${diff.plainCode ? " is-plain-code" : ""}`} onKeyDown={(e) => e.key === "Escape" && overlay && treeOpen && setTreeOpen(false)}>
      <h1 className="review-visually-hidden">Review changes: {item.title}</h1>
      <ItemHeader item={item} reload={reload} onSettings={() => toItem({ sel: { kind: "chain" }, tab: "config" })} onRunLog={() => toItem({ sel: { kind: "chain" } })} />
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
            onViewed={viewed.toggle}
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
          {compare.state === "ready" && (
            <DiffView
              files={files}
              patch={patch}
              prefs={diff.prefs}
              collapsed={collapsed}
              onCollapse={(path, c) => setCollapsed((s) => {
                const n = new Set(s);
                if (c) n.add(path);
                else n.delete(path);
                return n;
              })}
              selected={current}
              isViewed={viewed.isViewed}
              onViewed={viewed.toggle}
              threadCount={(path) => threadList.filter((t) => t.file_path === path && unresolved(t)).length}
              picked={picked}
              onPick={setPicked}
              onCompose={comments.openPick}
              onFileComment={(path) => {
                setCollapsed((s) => {
                  const n = new Set(s);
                  n.delete(path);
                  return n;
                });
                comments.openFile(path);
              }}
              after={comments.after}
              top={comments.top}
              truncated={compare.data.truncated ? { bytes: compare.data.diff_max_bytes, files: notShown.size } : null}
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
          approve={() => submit("approve", "")}
          onReviewChanges={(file) => setPlace({ doc: false, ...(file && { file }) })}
          onRequestChanges={() => {
            setPlace({ doc: false });
            setFinish({ outcome: "request_changes" });
          }}
          onClose={() => toItem({ sel: { kind: "chain" } })}
        />
      )}
      <BottomBar item={item} gate={place.gate} threads={threadList} onFinish={(outcome) => setFinish({ outcome })} submit={submit} />
      {finish && <FinishDialog item={item} gate={place.gate} threads={threadList} initial={finish.outcome} submit={submit} onClose={() => setFinish(null)} />}
    </div>
  );
}
