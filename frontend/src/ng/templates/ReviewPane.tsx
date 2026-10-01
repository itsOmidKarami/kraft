import { useState } from "react";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { Button } from "../ui/Button";
import { showToast } from "../ui/Toast";
import { folded, lineDiff } from "./draft/lineDiff";
import type { ConfigDraft } from "./draft/useConfigDraft";
import type { Problem, StaleBody } from "./draft/types";
import { chainFile, counts, liveChainFile } from "./draft/view";
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

/** Review & publish's pane (Decisions §9 Publish): the draft's changes and
 *  problems, the YAML diff, who it affects; Discard confirms in place;
 *  Publish waits for zero problems. A 409 shows the server's diff (R45). */
export function ReviewPane({ draft, chain, published, open, size, onCollapse, onExpand, onFix, onHighlight, onDone, onGone }: {
  draft: ConfigDraft;
  chain: string;
  /** The published file's text; null for a chain never published. */
  published: string | null | undefined;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  onFix: (path: string) => void;
  onHighlight: (path: string) => void;
  onDone: () => void;
  /** After a publish that moved the chain's file (a rename) or deleted it: where to go. */
  onGone?: (to: string | null) => void;
}) {
  const [tab, setTab] = useState("changes");
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState<Problem[] | null>(null);
  const view = draft.view!;
  const r = view.result;
  const n = counts(r);
  const blocked = n.problems > 0;
  const stale: StaleBody | null = draft.stale;
  const live = liveChainFile(view.files, chain);
  const text = view.files[live] ?? "";
  const problems = refused?.length ? refused : r.problems;

  // A rename moves the file to chains/<new id>.yaml; delete_chain makes it null.
  const renamedTo = live === chainFile(chain) ? chain : live.slice("chains/".length, -".yaml".length);
  const deleted = view.files[live] === null;
  const gone = deleted || renamedTo !== chain;
  const publish = async () => {
    const a = await draft.publish({ reload: !gone });
    if (a.status === 200) {
      showToast(deleted ? `Deleted ${chain}` : `Published ${renamedTo} · new items use it from now on`);
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
    const all = Object.values(stale?.files ?? {}).map((f) => f.draft).join("\n---\n") || text;
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
      id="chains-review"
      open={open}
      size={size}
      crumbs={[{ label: chain }]}
      icon="git-compare"
      title={`Draft · ${plural(n.changes, "change")}`}
      sub={stale ? "⚠ published since this draft began · your draft is kept" : blocked ? `✕ doesn't resolve · ${plural(n.problems, "problem")} block publishing` : "✓ resolves · ready to publish"}
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
                showToast(`Published ${chain} over the newer version`);
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
          {problems.map((p, i) => (
            <div key={i} className="tpl-rv-prob">
              <span className="tpl-rv-path">{p.path || chain}{p.field ? ` · ${p.field}` : ""}</span>
              <span>{problemText(p)}</span>
              <button type="button" className="tpl-rv-fix" onClick={() => onFix(p.path)}>Fix →</button>
            </div>
          ))}
        </>
      )}
      {tab === "changes" ? (
        <>
          <Head>Changes</Head>
          {!r.changes.length && <Note>{published === null ? "A new chain with no nodes yet." : "No changes."}</Note>}
          {r.changes.map((c, i) => (
            <button key={i} type="button" className="tpl-rv-change" onClick={() => onHighlight(c.path)}>
              <span className={`tpl-rv-sign is-${c.kind}`} aria-label={c.kind}>{c.kind === "add" ? "+" : c.kind === "remove" ? "−" : "~"}</span>
              <span className="tpl-rv-path">{c.path}</span>
              <span className="tpl-rv-sum">{c.summary}</span>
            </button>
          ))}
          <Head>Who it affects</Head>
          <Kv k="new items" v="use this version once published" />
          <Kv k="running" v={`${plural(r.impact.running ?? 0, "item")} keep the version they started on`} />
          <Kv k="repos" v={r.impact.repos?.length ? `${r.impact.repos.join(", ")} default to it` : "none default to it"} mono={!!r.impact.repos?.length} />
        </>
      ) : published === undefined ? (
        <Note>Loading the published file…</Note>
      ) : (
        <DiffLines lines={folded(lineDiff(published === null ? [] : published.split("\n"), text.split("\n"))).map((o) => ("s" in o ? o : { t: "…", s: "" }))} />
      )}
    </Inspector>
  );
}
