import { useState, type ReactNode } from "react";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { Button } from "../ui/Button";
import { showToast } from "../ui/Toast";
import { folded, lineDiff } from "./draft/lineDiff";
import type { ConfigDraft } from "./draft/useConfigDraft";
import type { Problem, Result, Scope, StaleBody } from "./draft/types";
import { chainFile, counts, LIBRARY_FILE, scopeFile } from "./draft/view";
import { resetLibrary, useLibrary } from "./useLibrary";
import { Head, Kv, Note } from "./panes/controls";
import { problemText } from "./problems";
import "./panes/panes.css";

const TABS = [{ value: "changes", label: "Changes" }, { value: "yaml", label: "YAML diff" }];
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** A unified diff's lines, tinted (the 409's server diff, or the YAML diff tab). */
function DiffLines({ lines }: { lines: { t: string; s: string }[] }) {
  return (
    <pre className="tpl-rv-diff">
      {lines.map((l, i) => (
        <span key={i} className={`tpl-rv-line${l.t === "+" ? " is-add" : l.t === "-" ? " is-del" : l.t === "…" ? " is-gap" : ""}`}>{l.t === "…" ? "⋯" : `${l.t} ${l.s}`}{"\n"}</span>
      ))}
    </pre>
  );
}
const serverDiff = (diff: string) =>
  diff.split("\n").filter((l) => l && !l.startsWith("---") && !l.startsWith("+++")).map((l) => (l.startsWith("@@") ? { t: "…", s: "" } : { t: l[0] === "+" || l[0] === "-" ? l[0] : " ", s: l.slice(1) }));

/** What an area that is not a chain or the library adds to the review pane (W15: repos,
 *  policy, intake): its name, the files it spans (each file's YAML diff is a block), who it
 *  affects, and the toast a publish shows. */
export interface ReviewArea {
  crumb: string;
  files: string[];
  toast: string;
  affects: (r: Result) => ReactNode;
}

/** Review & publish's pane (Decisions §9 Publish): the draft's changes and
 *  problems, the YAML diff, who it affects; Discard confirms in place;
 *  Publish waits for zero problems. A 409 shows the server's diff (R45). */
export function ReviewPane({ draft, scope, published, libraryPublished, area, open, size, onCollapse, onExpand, onFix, onHighlight, onDone, onGone, problemWhere }: {
  draft: ConfigDraft;
  scope: Scope;
  /** The published file's text; null for a chain never published. */
  published: string | null | undefined;
  /** The published `library.yaml`'s text, when a chain draft carries one (Move to library, R47): the second file's diff. */
  libraryPublished?: string | null;
  /** An area's review (repos, policy, intake): see `ReviewArea`. */
  area?: ReviewArea;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  onFix: (path: string, problem: Problem) => void;
  onHighlight: (path: string) => void;
  onDone: () => void;
  /** After a publish that moved the chain's file (a rename) or deleted it: where to go. */
  onGone?: (to: string | null) => void;
  /** Where a problem breaks, after its sentence: the Library names the chain, repo and component (Decisions §10). */
  problemWhere?: (p: Problem) => ReactNode;
}) {
  const [tab, setTab] = useState("changes");
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState<Problem[] | null>(null);
  const lib = scope.area === "library";
  const chain = area ? area.crumb : scope.key;
  const noun = lib ? "the library" : chain;
  const view = draft.view!;
  const r = view.result;
  const n = counts(r);
  const blocked = n.problems > 0;
  const stale: StaleBody | null = draft.stale;
  const live = scopeFile(view.files, scope);
  const text = view.files[live] ?? "";
  // A chain draft that moved a component into the library carries `library.yaml` too, and a publish writes both.
  const libText = !lib ? view.files[LIBRARY_FILE] : undefined;
  const joined = typeof libText === "string";
  const libraryList = useLibrary();
  const libModel = (view.result.model[LIBRARY_FILE] ?? {}) as Record<string, Record<string, unknown> | undefined>;
  const addedToLibrary = joined && typeof libraryList !== "string"
    ? ["nodes", "steps", "tasks", "steering"].flatMap((s) => Object.keys(libModel[s] ?? {}).map((n) => `${s}.${n}`)).filter((id) => !libraryList.some((c) => c.id === id))
    : [];
  const problems = refused?.length ? refused : r.problems;
  // How many of the changes reach each chain, as the server counts them.
  const reach = new Map<string, number>();
  for (const c of r.changes) for (const ch of c.reaches ?? []) reach.set(ch, (reach.get(ch) ?? 0) + 1);

  // A rename moves the file to chains/<new id>.yaml; delete_chain makes it null.
  const renamedTo = area || lib || live === chainFile(chain) ? chain : live.slice("chains/".length, -".yaml".length);
  const deleted = !area && view.files[live] === null;
  const gone = deleted || renamedTo !== chain;
  const publish = async () => {
    const a = await draft.publish({ reload: !gone });
    if (a.status === 200) {
      // The published library changed (a moved component joined it): menus read it afresh.
      if (joined) resetLibrary();
      showToast(area ? area.toast : deleted ? `Deleted ${chain}` : `Published ${lib ? "the library" : renamedTo}${joined ? " and the library" : ""} · new items use it from now on`);
      if (gone) onGone?.(deleted ? null : renamedTo);
      else onDone();
    } else if (a.status === 422) setRefused((a.body as { problems?: Problem[] }).problems ?? null);
  };
  const discard = async () => {
    setAsking(false);
    const a = await draft.discard();
    if (a.status === 204 || a.status === 404) {
      showToast("Draft discarded");
      onDone();
    }
  };
  const copy = async () => {
    const all = Object.values(stale?.files ?? {}).map((f) => f.draft).join("\n---\n") || (area ? area.files.map((f) => view.files[f] ?? "").join("\n---\n") : text);
    try {
      await navigator.clipboard.writeText(all);
      showToast("Copied the draft's YAML");
    } catch {
      showToast("Couldn't copy: the browser refused the clipboard");
    }
  };

  const footer = asking ? (
    <>
      <span className="tpl-rv-ask">Discard {plural(n.changes, "change")}?</span>
      <span className="bp-gap" />
      <Button onClick={() => setAsking(false)}>Keep</Button>
      <Button variant="danger" onClick={discard}>Discard</Button>
    </>
  ) : (
    <>
      <Button variant="danger" onClick={() => setAsking(true)}>Discard draft</Button>
      <span className="bp-gap" />
      <Button variant="primary" disabled={blocked} title={blocked ? `Fix the ${plural(n.problems, "problem")} first` : undefined} onClick={publish}>Publish</Button>
    </>
  );

  return (
    <Inspector
      id={area ? `${scope.area}-review` : lib ? "library-review" : "chains-review"}
      open={open}
      size={size}
      crumbs={[{ label: lib ? "Library" : chain }]}
      icon="git-compare"
      title={`Draft · ${plural(n.changes, "change")}`}
      sub={stale ? "⚠ published since this draft began · your draft is kept" : blocked ? `✕ ${lib ? "breaks something" : "doesn't resolve"} · ${plural(n.problems, "problem")} block publishing` : lib ? "✓ checked against every chain and repo · ready to publish" : `✓ resolves · ready to publish${joined ? " · writes library.yaml too" : ""}`}
      tabs={TABS}
      tab={tab}
      onTab={setTab}
      onCollapse={onCollapse}
      onExpand={onExpand}
      footer={footer}
    >
      {stale && (
        <div className="tpl-rv-stale" role="alert">
          <p className="tpl-rv-stale-head">Published since this draft began</p>
          <p className="tpl-rv-stale-text">{Object.keys(stale.files).join(", ")} changed on disk after this draft began. Publishing now would overwrite it, so nothing was written; your draft is kept.</p>
          {Object.entries(stale.files).map(([file, f]) => (
            <div key={file}>
              <p className="tpl-rv-file">{file}</p>
              <DiffLines lines={serverDiff(f.diff)} />
            </div>
          ))}
          <div className="tpl-rv-stale-acts">
            <Button onClick={copy}>Copy draft YAML</Button>
            <Button variant="danger" onClick={() => setAsking(true)}>Discard draft</Button>
            <Button variant="primary" disabled={blocked} onClick={async () => {
              const a = await draft.keepMine();
              if (a.status === 200) {
                showToast(`Published ${noun} over the newer version`);
                onDone();
              }
            }}>Keep my version and publish</Button>
          </div>
        </div>
      )}
      {r.warnings.map((w) => <Note key={w.file}>{w.file}: {w.message}.</Note>)}
      {(problems.length > 0 || r.yaml_error) && (
        <>
          <Head>Problems</Head>
          {r.yaml_error && <div className="tpl-rv-prob"><span className="tpl-rv-path">{r.yaml_error.file}, line {r.yaml_error.line}</span><span>{r.yaml_error.message}</span></div>}
          {lib && problems.some((p) => p.chain || p.repo) && <Note>A chain or repo problem is fixed in the library component it names.</Note>}
          {problems.map((p, i) => (
            <div key={i} className="tpl-rv-prob">
              <span className="tpl-rv-path">{p.path || chain}{p.field && p.field !== p.path ? ` · ${p.field}` : ""}</span>
              <span>{problemText(p)}</span>
              {problemWhere?.(p)}
              <button type="button" className="tpl-rv-fix" onClick={() => onFix(p.path, p)}>Fix →</button>
            </div>
          ))}
        </>
      )}
      {tab === "changes" ? (
        <>
          <Head>Changes</Head>
          {!r.changes.length && <Note>{!area && published === null ? "A new chain with no nodes yet." : "No changes."}</Note>}
          {r.changes.map((c, i) => (
            <button key={i} type="button" className="tpl-rv-change" onClick={() => onHighlight(c.path)}>
              <span className={`tpl-rv-sign is-${c.kind}`} aria-label={c.kind}>{c.kind === "add" ? "+" : c.kind === "remove" ? "−" : "~"}</span>
              <span className="tpl-rv-path">{c.path || "chain"}</span>
              <span className="tpl-rv-sum">{c.summary}{c.reaches?.length ? ` · reaches ${plural(c.reaches.length, "chain")}` : ""}</span>
            </button>
          ))}
          {joined && (
            <>
              <Head>library.yaml</Head>
              {addedToLibrary.map((id) => (
                <div key={id} className="tpl-rv-change">
                  <span className="tpl-rv-sign is-add" aria-label="add">+</span>
                  <span className="tpl-rv-path">{id}</span>
                  <span className="tpl-rv-sum">added to the library</span>
                </div>
              ))}
              {!addedToLibrary.length && <Note>The library file changed.</Note>}
            </>
          )}
          <Head>Who it affects</Head>
          {area ? area.affects(r) : <Kv k="new items" v="use this version once published" />}
          {area ? null : lib ? (
            <>
              <Kv k="chains" v={r.impact.chains?.length ? r.impact.chains.map((c) => `${c}${reach.get(c) ? ` · ${plural(reach.get(c)!, "change")}` : ""}`).join(", ") : "none"} mono={!!r.impact.chains?.length} muted={!r.impact.chains?.length} />
              <Kv k="repos" v={r.impact.repos?.length ? `${r.impact.repos.join(", ")} name a changed profile` : "none name a changed profile"} mono={!!r.impact.repos?.length} muted={!r.impact.repos?.length} />
              <Kv k="running" v="items keep the version they started on" />
            </>
          ) : (
            <>
              <Kv k="running" v={`${plural(r.impact.running ?? 0, "item")} keep the version they started on`} />
              <Kv k="repos" v={r.impact.repos?.length ? `${r.impact.repos.join(", ")} default to it` : "none default to it"} mono={!!r.impact.repos?.length} />
            </>
          )}
        </>
      ) : area ? (
        view.published === undefined ? <Note>Loading the published files…</Note> : (
          <>
            {area.files.filter((f) => (view.files[f] ?? null) !== (view.published![f] ?? null)).map((f) => (
              <div key={f}>
                <p className="tpl-rv-file">{f}</p>
                <DiffLines lines={folded(lineDiff((view.published![f] ?? "").split("\n"), (view.files[f] ?? "").split("\n"))).map((o) => ("s" in o ? o : { t: "…", s: "" }))} />
              </div>
            ))}
          </>
        )
      ) : published === undefined ? (
        <Note>Loading the published file…</Note>
      ) : (
        <>
          {joined && <p className="tpl-rv-file">{live}</p>}
          <DiffLines lines={folded(lineDiff(published === null ? [] : published.split("\n"), text.split("\n"))).map((o) => ("s" in o ? o : { t: "…", s: "" }))} />
          {joined && (
            <>
              <p className="tpl-rv-file">{LIBRARY_FILE}</p>
              {libraryPublished === undefined ? <Note>Loading the published library…</Note> : <DiffLines lines={folded(lineDiff(libraryPublished === null ? [] : libraryPublished.split("\n"), libText.split("\n"))).map((o) => ("s" in o ? o : { t: "…", s: "" }))} />}
            </>
          )}
        </>
      )}
    </Inspector>
  );
}
