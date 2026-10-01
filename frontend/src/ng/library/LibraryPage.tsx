import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { detailOf, request } from "../http";
import { isTextField } from "../keys";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { Button } from "../ui/Button";
import { showToast } from "../ui/Toast";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import { counts } from "../templates/draft/view";
import { YamlView } from "../templates/YamlView";
import { LibraryList } from "./LibraryList";
import { componentOf } from "./problemTarget";
import { listRows } from "./rows";
import { parseRef, refUrl, SECTION_LABEL, type PublishedLibrary, type Section } from "./types";
import "./library.css";

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
  const r = draft.view!.result;
  const [published] = usePublished();
  const [surface, setSurface] = useState<"canvas" | "yaml">("canvas");
  const [nextProblem, setNextProblem] = useState(0);
  const rows = useMemo(() => listRows(r, published === "failed" ? [] : published?.components ?? null), [r, published]);
  const sel = parseRef(refId);
  const id = sel ? `${sel.section}.${sel.name}` : undefined;
  const row = rows.find((x) => x.id === id);
  const n = counts(r);
  const yamlErr = r.yaml_error;

  useEffect(() => {
    if (draft.error) showToast(draft.error);
  }, [draft.error]);

  // ⌘Z undoes the last request, outside a text field.
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

  const add = async (section: Section, name: string, kind?: string) => {
    const a = await draft.ops([{ op: "add_component", section, name, ...(kind ? { kind } : {}) }], { quiet: true });
    if (a.status !== 200) return detailOf(a.body);
    navigate(refUrl(`${section}.${name}`));
    return null;
  };

  const stepProblems = () => {
    const ids = [...new Set(r.problems.map(componentOf).filter((x): x is string => !!x && rows.some((y) => y.id === x)))];
    if (!ids.length) return;
    navigate(refUrl(ids[nextProblem % ids.length]));
    setNextProblem(nextProblem + 1);
  };

  const toggleYaml = () => {
    // A syntax error keeps you here until it is fixed or reverted, as in Chains.
    if (surface === "yaml") return setSurface("canvas");
    draft.flush();
    setSurface("yaml");
  };

  return (
    <div className="lib-page" data-ref={refId}>
      <HeaderTail>
        {sel && (
          <>
            <span className="lib-sep" aria-hidden>›</span>
            <span className="lib-ref">{sel.name}</span>
          </>
        )}
        {draft.view!.draft
          ? <span className="lib-draft">DRAFT · {n.changes} {n.changes === 1 ? "CHANGE" : "CHANGES"}</span>
          : <span className="lib-published">published</span>}
        {n.problems > 0 && (
          <button type="button" className="lib-problems" title="Step through the problems" onClick={stepProblems}>
            {n.problems} {n.problems === 1 ? "PROBLEM" : "PROBLEMS"}
          </button>
        )}
      </HeaderTail>
      <HeaderActions>
        <Button aria-pressed={surface === "yaml"} disabled={surface === "yaml" && !!yamlErr} title={surface === "yaml" && yamlErr ? `Fix line ${yamlErr.line} first, or revert` : undefined} onClick={toggleYaml}>
          {surface === "yaml" ? "⇄ Canvas" : "YAML"}
        </Button>
      </HeaderActions>
      <LibraryList rows={rows} selected={id} onSelect={(x) => navigate(refUrl(x))} onAdd={add} />
      <main className="lib-main">
        {surface === "yaml" ? (
          <div className="lib-yaml">
            <YamlView draft={draft} scope={draft.scope} published={published === null ? undefined : published === "failed" ? null : published.text} />
          </div>
        ) : (
          <div className="lib-canvas">
            <Button className="lib-back" onClick={() => navigate("/templates/library")}>← Library</Button>
            {!refId ? <p>Pick a component.</p> : !sel || (rows.length > 0 && !row) ? <p>There is no component called {refId}.</p> : row ? <p><span className="lib-ref">{row.name}</span> · {SECTION_LABEL[row.section].toLowerCase()}</p> : null}
          </div>
        )}
      </main>
    </div>
  );
}
