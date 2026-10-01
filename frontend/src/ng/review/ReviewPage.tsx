import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { ItemHeader } from "../item/header/ItemHeader";
import { placeUrl } from "../item/url";
import { useItem, type ItemDetail } from "../item/useItem";
import { useOverlay } from "../graph/useResizable";
import { Button } from "../ui/Button";
import { FileTree } from "./FileTree";
import { byNodes } from "./model";
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
  const [, setCollapsed] = useState<Set<string> | "all">(new Set());
  const viewed = useViewed(item.id, place.to, compare);
  const all = compare.state === "ready" ? compare.data.files : [];
  const files = byNodes(all, place.nodes);
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
        onCollapseAll={() => setCollapsed("all")}
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
            notShown={new Set()}
            threads={threads.state === "ready" ? threads.data : []}
            selected={place.file}
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
          {compare.state === "ready" && !files.length && <p className="rv-empty">No files match this comparison.</p>}
        </section>
      </div>
    </div>
  );
}
