import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useResizable } from "../graph/useResizable";
import { detailOf, request } from "../http";
import { isTextField } from "../keys";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { Button } from "../ui/Button";
import { NOTHING_TO_PUBLISH } from "../ui/reasons";
import { NotFound } from "../ui/NotFound";
import { showToast } from "../ui/Toast";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import { counts } from "../templates/draft/view";
import { useBox } from "../templates/ChainsPage";
import { ReviewPane } from "../templates/ReviewPane";
import { DraftLibrary, resetLibrary } from "../templates/useLibrary";
import { YamlView } from "../templates/YamlView";
import { LibraryCanvas } from "./LibraryCanvas";
import { LibraryLanding } from "./LibraryLanding";
import { LibraryList } from "./LibraryList";
import { LibraryPane } from "./LibraryPane";
import { componentOf } from "./problemTarget";
import { ProblemWhere } from "./ProblemWhere";
import { listRows, withAddons } from "./rows";
import { AddonBadge, ReadOnly } from "../templates/addon";
import { LIBRARY_FILE } from "../templates/draft/view";
import { addonRepo, parseRef, refUrl, SECTIONS, type PublishedLibrary, type Section } from "./types";
import "./library.css";
import { usePane } from "../shell/usePane";

/** The published library, for its used-by figures and its text (the YAML view's left side). */
function usePublished() {
  const [lib, setLib] = useState<PublishedLibrary | null | "failed">(null);
  const load = useCallback(() => request<PublishedLibrary>("/templates/library").then((a) => setLib(a.status === 200 ? a.body : "failed")), []);
  useEffect(() => void load(), [load]);
  return [lib, load] as const;
}

/** The Library (Decisions §10): the library's one server-side draft, a list of
 *  its components, and the selected one. */
export function LibraryPage() {
  const { ref } = useParams();
  const draft = useConfigDraft("library", "library");
  if (draft.status === "error" && !draft.view) return <div className="lib-note" role="alert">Couldn't load the library. {draft.error}</div>;
  if (!draft.view) return <div className="lib-note">Loading the library…</div>;
  return <Editor refId={ref} draft={draft} />;
}

function Editor({ refId, draft }: { refId: string | undefined; draft: ConfigDraft }) {
  const navigate = useNavigate();
  // A landing tile's section (`?section=tasks`) narrows the list and rides along while a component is picked.
  const [params, setParams] = useSearchParams();
  const asked = params.get("section");
  const only = SECTIONS.find((s) => s === asked);
  // Where a component lives: the same section narrowing rides along while the target is in it.
  const go = (ref: string) => ({ pathname: refUrl(ref), search: only && ref.split(".")[0] === only ? `?section=${only}` : "" });
  const r = draft.view!.result;
  const [published, reloadPublished] = usePublished();
  // Review & publish takes the pane's place (Decisions §10 Publish); the canvas stays.
  const [review, setReview] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true);
  const [surface, setSurface] = useState<"canvas" | "yaml">("canvas");
  const [nextProblem, setNextProblem] = useState(0);
  // The part of the component picked (a step or task inside a node), or null for the component itself, and
  // whether the pane shows it; a pane the person collapsed stays collapsed through picks (Decisions §9 Selection).
  const [sub, setSub] = useState<string | null>(null);
  const [paneOpen, setPaneOpen] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const [frame, mainW, mainH] = useBox();
  const size = useResizable("library", mainW);
  const addons = draft.view!.addon_library;
  const rows = useMemo(() => listRows(r, published === "failed" ? [] : published?.components ?? null, addons), [r, published, addons]);
  const merged = useMemo(() => withAddons(r, addons), [r, addons]);
  const sel = parseRef(refId);
  const id = sel ? `${sel.section}.${sel.name}` : undefined;
  const row = rows.find((x) => x.id === id);
  usePane(paneOpen && !!row);
  // An add-on's component is read through the same accessors, over the add-ons' library beside the draft's: nothing
  // in it is the draft's, so it has no change and no problem, and every write to it would answer 409.
  const shownDraft = useMemo(() => (row?.addon ? { ...draft, view: { ...draft.view!, result: { ...r, model: { ...r.model, [LIBRARY_FILE]: merged }, problems: [], changes: [] } } } : draft), [row?.addon, draft, r, merged]);
  // What the pane shows now, for a rename that lands after another pick.
  const shown = useRef("");
  shown.current = sub ?? row?.id ?? "";
  const n = counts(r);
  const yamlErr = r.yaml_error;
  const publishedId = useId();

  useEffect(() => {
    if (draft.error) showToast(draft.error, { tone: "error" });
  }, [draft.error]);
  // A new component starts at its root, pane open.
  useEffect(() => {
    setSub(null);
    setPaneOpen(!collapsed);
    // Only when the component changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  const pick = (p: string) => {
    setSub(p === id ? null : p);
    setPaneOpen(!collapsed);
  };
  const expand = (p?: string) => {
    if (p) setSub(p === id ? null : p);
    setPaneOpen(true);
    setCollapsed(false);
  };
  const collapse = () => {
    setPaneOpen(false);
    setCollapsed(true);
  };
  const reserve = size.overlay ? 0 : (review ? reviewOpen : paneOpen) ? size.width : 40;
  // A component the published library does not list (one just added) has no uses yet.
  const uses = published === null || published === "failed" ? null : published.components.find((c) => c.id === id)?.used_by_paths ?? [];
  // What the menus offer: the draft's own components, so one just added or renamed is there before it is published.
  const draftLibrary = useMemo(() => rows.map((x) => ({ id: x.id, kind: x.section, name: x.name, definition: merged[x.section]?.[x.name] ?? {}, used_by: [], issues: [] })), [rows, merged]);

  // ⌘Z undoes the last request, outside a text field. Not over an add-on's component: nothing shown there would change.
  const undo = draft.undo;
  const readOnly = !!row?.addon;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (readOnly || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "z" || isTextField(e.target)) return;
      e.preventDefault();
      undo();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [undo, readOnly]);

  const add = async (section: Section, name: string, kind?: string) => {
    const a = await draft.ops([{ op: "add_component", section, name, ...(kind ? { kind } : {}) }], { quiet: true });
    if (a.status !== 200) return detailOf(a.body);
    navigate(go(`${section}.${name}`));
    return null;
  };

  const stepProblems = () => {
    const ids = [...new Set(r.problems.map(componentOf).filter((x): x is string => !!x && rows.some((y) => y.id === x)))];
    if (!ids.length) return;
    navigate(go(ids[nextProblem % ids.length]));
    setNextProblem(nextProblem + 1);
  };

  const toggleYaml = () => {
    // A syntax error keeps you here until it is fixed or reverted, as in Chains.
    if (surface === "yaml") return setSurface("canvas");
    draft.flush();
    setSurface("yaml");
  };

  return (
    <DraftLibrary.Provider value={draftLibrary}>
    <div className="lib-page" data-ref={refId}>
      <HeaderTail>
        {row && (
          <>
            <span className="lib-sep" aria-hidden>›</span>
            <span className="lib-ref">{row.name}</span>
          </>
        )}
        {row?.addon && <AddonBadge addon={row.addon} />}
        {draft.view!.draft
          ? <span className="lib-draft">DRAFT · {n.changes} {n.changes === 1 ? "CHANGE" : "CHANGES"}</span>
          : <><span className="lib-published" id={publishedId}>published</span><span className="reason is-quiet" id={`${publishedId}-why`}>{NOTHING_TO_PUBLISH}</span></>}
        {n.problems > 0 && (
          <button type="button" className="lib-problems" data-tip="Step through the problems" onClick={stepProblems}>
            {n.problems} {n.problems === 1 ? "PROBLEM" : "PROBLEMS"}
          </button>
        )}
      </HeaderTail>
      <HeaderActions>
        <Button aria-pressed={surface === "yaml"} disabled={surface === "yaml" && !!yamlErr} title={surface === "yaml" && yamlErr ? `Fix line ${yamlErr.line} first, or revert` : undefined} onClick={toggleYaml}>
          {surface === "yaml" ? "⇄ Canvas" : "YAML"}
        </Button>
        {surface === "canvas" && <Button variant={review ? undefined : "primary"} disabled={!review && !draft.view!.draft} aria-describedby={draft.view!.draft || review ? undefined : `${publishedId} ${publishedId}-why`} onClick={() => { setReview((v) => !v); setReviewOpen(true); }}>{review ? "Back to editing" : "Review & publish"}</Button>}
      </HeaderActions>
      <LibraryList rows={rows} selected={id} only={only} onShowAll={() => setParams({}, { replace: true })} onSelect={(x) => navigate(go(x))} onAdd={add} />
      <div className="lib-main" ref={frame}>
        {surface === "yaml" ? (
          <div className="lib-yaml">
            <YamlView draft={draft} scope={draft.scope} published={published === null ? undefined : published === "failed" ? null : published.text} />
          </div>
        ) : (
          <div className={`lib-canvas${refId ? "" : " is-landing"}`} style={{ right: reserve }}>
            <Button className="lib-back" onClick={() => navigate("/templates/library")}>← Library</Button>
            {!refId ? <LibraryLanding rows={rows} loaded={published !== null} /> : !row ? <NotFound what="component" id={refId} backTo="/templates/library" backLabel="Back to the Library" /> : null}
          </div>
        )}
        {surface === "canvas" && row && (
          <ReadOnly.Provider value={!!row.addon}>
            <LibraryCanvas
              key={row.id}
              draft={shownDraft}
              row={row}
              uses={uses}
              path={sub ?? row.id}
              reserve={reserve}
              height={mainH}
              onPick={pick}
              onOpen={(p) => expand(p)}
              onEscape={paneOpen ? collapse : () => {}}
              onBackground={() => pick(row.id)}
              onGoTo={(x) => navigate(go(x))}
            />
            {review ? null : <LibraryPane
              draft={shownDraft}
              path={sub ?? row.id}
              uses={uses}
              names={rows.filter((x) => x.section === row.section).map((x) => x.name)}
              open={paneOpen}
              size={size}
              goTo={(p) => expand(p)}
              onLibrary={() => navigate("/templates/library")}
              onRenamed={(from, to) => {
                // A pick made since (a click elsewhere committed the rename) wins; one inside what was renamed follows it.
                const cur = shown.current;
                if (cur !== from && !cur.startsWith(`${from}.`)) return;
                // A component's rename moves the URL; a part of one moves the selection.
                if (to.split(".").length <= 2) navigate(go(to), { replace: true });
                else setSub(to + cur.slice(from.length));
              }}
              onRemoved={(removed) => {
                if (removed.split(".").length > 2) return setSub(null);
                // The next row of the list takes the selection, else the previous, else the empty list.
                const i = rows.findIndex((x) => x.id === removed);
                const next = rows[i + 1] ?? rows[i - 1];
                navigate(next ? go(next.id) : "/templates/library", { replace: true });
              }}
              onDuplicated={(to) => navigate(go(to))}
              onCollapse={collapse}
              onExpand={() => expand()}
              copyable={!addonRepo(row.addon)}
            />}
          </ReadOnly.Provider>
        )}
        {surface === "canvas" && review && (
          <ReviewPane
            draft={draft}
            scope={draft.scope}
            published={published === null ? undefined : published === "failed" ? null : published.text}
            open={reviewOpen}
            size={size}
            onCollapse={() => setReviewOpen(false)}
            onExpand={() => setReviewOpen(true)}
            onHighlight={(path) => { const c = path.split(".").slice(0, 2).join("."); setReview(false); if (rows.some((x) => x.id === c)) navigate(go(c)); }}
            onFix={(path, p) => {
              // A problem is fixed in the library component it names, else the one its own path names.
              const target = componentOf(p) ?? path.split(".").slice(0, 2).join(".");
              setReview(false);
              if (rows.some((x) => x.id === target)) navigate(go(target));
            }}
            onDone={() => { setReview(false); resetLibrary(); void reloadPublished(); }}
            problemWhere={(p) => <ProblemWhere p={p} />}
          />
        )}
      </div>
    </div>
    </DraftLibrary.Provider>
  );
}
