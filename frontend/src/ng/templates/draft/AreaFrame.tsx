import { useEffect, useRef, useState, type ReactNode } from "react";
import { useResizable, useWidth } from "../../graph/useResizable";
import { isTextField } from "../../keys";
import { HeaderActions, HeaderTail } from "../../shell/HeaderActions";
import { Button } from "../../ui/Button";
import { ReviewPane, type ReviewArea } from "../ReviewPane";
import { YamlView } from "../YamlView";
import type { ConfigDraft } from "./useConfigDraft";
import type { Problem } from "./types";
import { counts } from "./view";
import "../templates.css";

export interface FrameCtx {
  size: ReturnType<typeof useResizable>;
  /** Review & publish is open: the page leaves its own pane to it. */
  review: boolean;
  /** The width the page's body gives up to a side pane, the review pane's while reviewing. */
  reserve: (ownPaneOpen: boolean) => number;
  /** Opens the YAML view on one of the area's files (an "Edit in YAML" link in a pane). */
  yaml: (file: string) => void;
}

/** What the three settings areas (Repos, Policy, Auto-intake) share: the draft
 *  state and problems badge in the header's tail, YAML and Review & publish as
 *  its actions, ⌘Z, the area's file(s) in the shared YamlView and the shared
 *  review pane. The page draws its body, and its own pane, from `children`. */
export function AreaFrame({ draft, area, pageKey, title, tail, actions, onFix, onHighlight, children }: {
  draft: ConfigDraft;
  area: ReviewArea;
  /** The key the pane width is remembered under (`useResizable`). */
  pageKey: string;
  /** The page's heading, read by a screen reader only: the crumbs are the visible one. */
  title: string;
  /** Before the draft state in the header's tail (Policy's section menu). */
  tail?: ReactNode;
  /** Header buttons before YAML. */
  actions?: ReactNode;
  /** Fix → in the review pane and the problems badge: select what the problem is about. */
  onFix: (problem: Problem) => void;
  onHighlight?: (path: string) => void;
  children: (ctx: FrameCtx) => ReactNode;
}) {
  const view = draft.view!;
  const r = view.result;
  const n = counts(r);
  const [frame, w] = useWidth();
  const size = useResizable(pageKey, w);
  const [review, setReview] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [surface, setSurface] = useState<"page" | "yaml">("page");
  const [file, setFile] = useState(area.files[0]);
  const next = useRef(0);

  // ⌘Z undoes the last request, outside a text field (W10 Decided 4).
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

  // Escape leaves Review and the YAML view, as it does on the chain's page.
  useEffect(() => {
    if (!review && surface === "page") return;
    const on = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || isTextField(e.target) || e.defaultPrevented) return;
      if (review) setReview(false);
      else if (!r.yaml_error) setSurface("page");
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [review, surface, r.yaml_error]);

  const toggleReview = () => {
    setSurface("page");
    setReviewOpen(true);
    setReview((v) => !v);
  };
  const toggleYaml = () => {
    if (surface === "yaml") return setSurface("page");
    draft.flush();
    setReview(false);
    setSurface("yaml");
  };
  const yamlErr = r.yaml_error;
  const openYaml = (f: string) => {
    draft.flush();
    setReview(false);
    if (area.files.includes(f)) setFile(f);
    setSurface("yaml");
  };
  const reserve = (own: boolean) => {
    const open = review ? reviewOpen : own;
    return size.overlay ? 0 : open ? size.width : 40;
  };
  const step = () => {
    if (!r.problems.length) return;
    setReview(false);
    setSurface("page");
    onFix(r.problems[next.current++ % r.problems.length]);
  };

  return (
    <div className="tpl-page" ref={frame}>
      <h1 className="adr-sr">{title}</h1>
      <HeaderTail>
        {tail}
        {view.draft
          ? <span className="tpl-draft">DRAFT · {n.changes} {n.changes === 1 ? "CHANGE" : "CHANGES"}</span>
          : <span className="tpl-published">published</span>}
        {n.problems > 0 && (
          <button type="button" className="tpl-problems" title="Step through the problems" onClick={step}>
            {n.problems} {n.problems === 1 ? "PROBLEM" : "PROBLEMS"}
          </button>
        )}
      </HeaderTail>
      <HeaderActions>
        {!review && actions}
        {!review && (
          <Button aria-pressed={surface === "yaml"} disabled={surface === "yaml" && !!yamlErr} title={surface === "yaml" && yamlErr ? `Fix line ${yamlErr.line} first, or revert` : undefined} onClick={toggleYaml}>
            {surface === "yaml" ? "⇄ Page" : "YAML"}
          </Button>
        )}
        <Button variant="primary" aria-pressed={review} disabled={!review && n.changes === 0 && n.problems === 0} onClick={toggleReview}>Review &amp; publish</Button>
      </HeaderActions>
      <div className="tpl-area">
        {surface === "yaml" ? (
          <>
            {area.files.length > 1 && (
              <div className="adr-files" role="tablist" aria-label="Files">
                {area.files.map((f) => (
                  <button key={f} type="button" role="tab" aria-selected={f === file} className={`adr-file${f === file ? " is-on" : ""}`} onClick={() => { draft.flush(); setFile(f); }}>{f}</button>
                ))}
              </div>
            )}
            <div className={area.files.length > 1 ? "adr-yaml has-files" : "adr-yaml"}>
              <YamlView key={file} draft={draft} scope={draft.scope} file={file} published={view.published === undefined ? undefined : view.published[file] ?? null} />
            </div>
          </>
        ) : (
          <>
            {children({ size, review, reserve, yaml: openYaml })}
            {review && (
              <ReviewPane
                draft={draft}
                scope={draft.scope}
                area={area}
                published={undefined}
                open={reviewOpen}
                size={size}
                onCollapse={() => setReviewOpen(false)}
                onExpand={() => setReviewOpen(true)}
                onHighlight={(p) => onHighlight?.(p)}
                onFix={(_, p) => {
                  setReview(false);
                  if (p) onFix(p);
                }}
                onDone={() => setReview(false)}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
