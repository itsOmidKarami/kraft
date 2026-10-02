import { useEffect, useRef, useState, type InputHTMLAttributes } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../../api";
import { repoName } from "../../format";
import { useStore } from "../../store";
import type { ChainNode, Repo, SearchResult, TemplateSummary } from "../../types";
import { NodeGlyph } from "../graph/NodeGlyph";
import { detailOf, jsonBody, request } from "../http";
import { useFocusSoon } from "../item/useFocusSoon";
import { Menu } from "../ui/Menu";
import { Popover } from "../ui/Popover";
import { showToast } from "../ui/Toast";
import { Ticks } from "./Ticks";
import { sendOnModEnter } from "../keys";

type Kind = "spec" | "plan";
/** B33's answer: the chain as it would be filed, and what the attachments and skips dropped. */
type DryRun = { nodes: ChainNode[]; skipped: { node: string; why: string; kind?: string }[]; gates: string[] };
export type Draft = { title: string; brief: string; repo: string; chain: string; spec: string; plan: string };

/** The body both the preview and Create send (B33 takes the create body). */
export function createBody(d: Draft, autostart: boolean) {
  const attachments = (["spec", "plan"] as Kind[]).filter((k) => d[k].trim()).map((k) => ({ kind: k, path: d[k].trim() }));
  return { title: d.title.trim(), description: d.brief.trim(), repo: d.repo, chain_template: d.chain, attachments, autostart };
}

/** What `POST /work-items` answers. `slots` comes with an autostart the server filed paused because every slot was busy. */
export type Created = { id: string; status?: string; duplicate_warning?: string; slots?: { busy: number; limit: number } };

/** Create and start that the server filed paused (every slot busy) says so,
 *  rather than leaving a "Not started" row to explain itself. Nothing starts
 *  it later on its own: its Start is on the board. */
export function sayIfFiledPaused(autostart: boolean, body: Created): void {
  if (!autostart || body.status !== "paused") return;
  const s = body.slots;
  showToast(`Filed paused: ${s ? `${s.busy} of ${s.limit} slots are` : "every slot is"} busy. Start it when one frees.`, 8000);
}

/** What will stop an item filed on `repo`, said before it is filed (the server's
 *  `repo_warning` says it after): no setup command declared, or no test command. */
export function repoStops(repo: Repo): string | null {
  const stops = [
    ...(repo.setup_command === null ? ["declares no setup command, so an item stops before its first task"] : []),
    ...(repo.test_command === null && !repo.test_scopes?.length ? ["has no test command, so an item runs its agent tasks, then stops at verification"] : []),
  ];
  return stops.length ? `${repoName(repo.path)} ${stops.join("; and it ")}.` : null;
}

const looksLikePath = (s: string) => /[/.]/.test(s) && !/\s/.test(s.trim());

/** The composer at the top of the board (Decisions §7b, AreaBoard 37–62):
 *  title, a line of brief, repo, chain, an optional spec or plan, and a tick
 *  preview of what will run from the server's dry run. ⌘↵ creates and starts. */
export function Composer({ repoFilter, onClose, onCreated }: { repoFilter: string; onClose: () => void; onCreated: (id: string) => void }) {
  const navigate = useNavigate();
  /** The enabled repos, null until read: an empty list is no repo to file to. */
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [roots, setRoots] = useState<Set<string>>(new Set());
  const [chains, setChains] = useState<TemplateSummary[]>([]);
  const [d, setD] = useState<Draft>({ title: "", brief: "", repo: "", chain: "", spec: "", plan: "" });
  const [ask, setAsk] = useState(false);
  const [preview, setPreview] = useState<{ run: DryRun } | { error: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }));
  const dirty = !!(d.title.trim() || d.brief.trim());

  useEffect(() => {
    api.getRepos().then((r) => {
      const live = r.repos.filter((x) => x.enabled !== false);
      setRepos(live);
      // Repos that root a workspace with members: those can file a cross-repo item (on the draft page).
      const byId = (id: string) => r.repos.find((x) => x.id === id);
      setRoots(new Set(Object.values(r.workspaces ?? {}).filter((w) => Object.keys(w.members).length).map((w) => byId(w.root)?.path ?? "")));
      const first = live.find((x) => x.path === repoFilter) ?? live[0];
      if (first) setD((x) => (x.repo ? x : { ...x, repo: first.path, chain: first.default_chain_template }));
    }).catch(() => {});
    api.getTemplates().then(setChains).catch(() => {});
  }, [repoFilter]);

  // B33: what will run, read again 300ms after the repo, chain or an attachment changes.
  useEffect(() => {
    if (!d.repo || !d.chain) return;
    const t = setTimeout(async () => {
      const body = { ...createBody(d, false), title: d.title.trim() || "draft" };
      const r = await request<DryRun>("/work-items?dry_run=1", jsonBody("POST", body));
      setPreview(r.status === 200 ? { run: r.body } : { error: detailOf(r.body) });
    }, 300);
    return () => clearTimeout(t);
    // The title and brief do not change what runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.repo, d.chain, d.spec, d.plan]);

  // Esc: a menu first (its own), then the discard ask, then the composer.
  const leave = () => (dirty ? setAsk(true) : onClose());
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      if (ask) setAsk(false);
      else leave();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // More options hands the draft to the draft item page (Decisions §7b): title, brief, repo, chain and attachments.
  const more = (members: boolean) => {
    onClose();
    navigate("/work-items/new", { state: { draft: d, members } });
  };
  const create = async (autostart: boolean) => {
    if (!d.title.trim() || !d.repo || busy || (preview && "error" in preview)) return;
    setBusy(true);
    setError(null);
    const r = await request<Created>("/work-items", jsonBody("POST", createBody(d, autostart)));
    setBusy(false);
    if (r.status !== 201 && r.status !== 200) return setError(detailOf(r.body));
    if (r.body.duplicate_warning) showToast(r.body.duplicate_warning, 6000);
    sayIfFiledPaused(autostart, r.body);
    await useStore.getState().bootstrap().catch(() => {});
    onCreated(r.body.id);
  };

  const full = chains.find((c) => c.id === d.chain)?.nodes ?? [];
  const run = preview && "run" in preview ? preview.run : null;
  const off = new Set(run?.skipped.map((s) => s.node) ?? []);
  const ran = run ? run.nodes.length : full.length;
  const gates = run ? run.gates.length : full.filter((n) => n.kind === "gate").length;
  const repo = repos?.find((r) => r.path === d.repo);
  const ok = !!d.title.trim() && !!d.repo && !busy && !(preview && "error" in preview);

  return (
    <section className="composer" aria-label="New work item" onKeyDown={sendOnModEnter(() => create(true), !busy)}>
      <div className="composer-row">
        <NodeGlyph kind="slot" size="sm" mark="add" />
        <input className="composer-title" aria-label="Title" placeholder="Title" autoFocus value={d.title} onChange={(e) => set({ title: e.target.value })} />
        <span className="composer-hint">⌘↵ create and start</span>
      </div>
      <div className="composer-indent">
        <textarea className="composer-brief" aria-label="Brief" placeholder="A line of brief. Every node reads it." rows={1} value={d.brief} onChange={(e) => set({ brief: e.target.value })} />
      </div>
      <div className="composer-indent composer-chips">
        <Menu
          label="Repo"
          triggerClass="composer-chip"
          trigger={<>{repo ? repoName(repo.path) : "repo"} <span aria-hidden className="board-caret">▾</span></>}
          items={(repos ?? []).map((r) => ({ label: repoName(r.path), checked: r.path === d.repo, hint: r.default_chain_template, onSelect: () => set({ repo: r.path, chain: r.default_chain_template, spec: "", plan: "" }) }))}
        />
        <Menu
          label="Chain"
          triggerClass="composer-chip composer-mono"
          trigger={<>{d.chain || "chain"} <span className="composer-muted">{full.length} nodes</span> <span aria-hidden className="board-caret">▾</span></>}
          items={chains.filter((c) => !c.error).map((c) => ({
            label: c.id,
            checked: c.id === d.chain,
            hint: `${c.nodes.length} nodes · ${c.gates} gates${c.id === repo?.default_chain_template ? " · repo default" : ""}`,
            onSelect: () => set({ chain: c.id }),
          }))}
        />
        {(["spec", "plan"] as Kind[]).map((k) => <Attach key={k} kind={k} repo={d.repo} path={d[k]} onPath={(p) => set({ [k]: p })} />)}
        {roots.has(d.repo) && <button type="button" className="composer-add composer-cross" title="Choose members on the full page" onClick={() => more(true)}>Cross-repo <span className="composer-muted">· on the page</span></button>}
        <span className="composer-gap" />
        <span className="composer-run">{ran} of {full.length || ran} nodes run · {gates} gate{gates === 1 ? "" : "s"}</span>
        <Ticks className="composer-ticks" ticks={(full.length ? full : run?.nodes ?? []).map((n) => ({ gate: n.kind === "gate", state: off.has(n.id) ? "todo" : "run" }))} />
      </div>
      {preview && "error" in preview && <p className="composer-indent item-error" role="alert">{preview.error}</p>}
      {repo && repoStops(repo) && <p className="composer-indent composer-note">{repoStops(repo)} Set it in <Link to="/templates/repos" className="item-link">Templates › Repos</Link> before you start it.</p>}
      <div className="composer-foot">
        {ask ? (
          <>
            <span className="composer-ask">Discard this draft? Nothing has been created.</span>
            <button type="button" className="btn btn-danger" onClick={onClose}>Discard</button>
            <button type="button" className="btn btn-secondary" autoFocus onClick={() => setAsk(false)}>Keep editing</button>
          </>
        ) : (
          <>
            <span className="composer-note">
              {error ? <span className="item-error" role="alert">{error}</span>
                : repos?.length === 0 ? <>No enabled repo to file to. Connect or enable one in <Link to="/templates/repos" className="item-link">Templates › Repos</Link>.</>
                : "Created paused. Nothing spends tokens until you start it."}
            </span>
            <button type="button" className="board-select-all" title="Continue on the full page" onClick={() => more(false)}>More options ⤢</button>
            <button type="button" className="board-select-all" onClick={leave}>Cancel</button>
            <CreateSplit disabled={!ok} onCreate={create} />
          </>
        )}
      </div>
    </section>
  );
}

/** Create paused ▾, whose panel holds Create and start (Decisions §7b Create).
 *  The toggle opens on focus or click, and focus stays on it; Enter, Space or
 *  ↓ there opens it with focus on Create and start, as the item page's main
 *  button does. Escape hands focus back to the toggle without reopening it. */
export function CreateSplit({ disabled, onCreate }: { disabled: boolean; onCreate: (start: boolean) => void }) {
  const group = useRef<HTMLSpanElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  // Opened from a key: the popover moves focus in.
  const [keyed, setKeyed] = useState(false);
  // Focus handed back to the toggle must not reopen the panel.
  const quiet = useRef(false);
  const close = () => {
    setOpen(false);
    setKeyed(false);
    if (list.current?.contains(document.activeElement)) {
      quiet.current = true;
      toggle.current?.focus();
    }
  };
  return (
    <span ref={group} className="composer-split">
      <button type="button" className="btn btn-secondary composer-create" disabled={disabled} onClick={() => onCreate(false)}>Create paused</button>
      <button
        ref={toggle}
        type="button"
        className="btn btn-secondary composer-toggle"
        aria-label="More ways to create"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onFocus={() => (quiet.current ? (quiet.current = false) : setOpen(true))}
        onClick={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key !== "ArrowDown" && e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          setKeyed(true);
          setOpen(true);
        }}
      >
        ▾
      </button>
      <Popover anchor={group} open={open && !disabled} onClose={close} role="menu" label="More ways to create" focusIn={keyed}>
        <div ref={list} className="menu">
          <button type="button" role="menuitem" tabIndex={-1} className="menu-item" onClick={() => { close(); onCreate(true); }}>Create and start</button>
        </div>
      </Popover>
    </span>
  );
}

/** A spec or plan slot: type to search the repo's documents (GET /search,
 *  the shipped composer's call), or paste a repo-relative path. A list
 *  before typing has no API source (Kraft-sob3o). */
export function Attach({ kind, repo, path, onPath }: { kind: Kind; repo: string; path: string; onPath: (p: string) => void }) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchResult[]>([]);
  useEffect(() => {
    if (!q.trim()) return setHits([]);
    const t = setTimeout(() => {
      // The index files documents under the plural (`specs`, `plans`), as the
      // shipped composer's docKind did; `spec` matches nothing.
      api.search({ q: q.trim(), repo, kind: `${kind}s`, source_kind: "artifact", limit: 5 }).then((r) => setHits(r.results), () => setHits([]));
    }, 200);
    return () => clearTimeout(t);
  }, [q, repo, kind]);
  const pick = (p: string) => {
    onPath(p);
    setOpen(false);
    setQ("");
  };
  if (path)
    return (
      <span className="composer-chip composer-mono composer-attached" title={path}>
        <span className="composer-path">{kind} · {path}</span>
        <button type="button" className="composer-x" aria-label={`Remove the ${kind}`} onClick={() => onPath("")}>✕</button>
      </span>
    );
  return (
    <>
      <button ref={anchor} type="button" className="composer-add" aria-expanded={open} onClick={() => setOpen((o) => !o)}>+ {kind}</button>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} role="dialog" label={`Attach a ${kind}`}>
        <div className="composer-attach">
          <SearchBox
            aria-label={`Search ${kind}s, or paste a repo-relative path`}
            placeholder={`search ${kind}s, or paste a path`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              // A pasted path is what the person means; otherwise the first hit.
              if (looksLikePath(q)) pick(q.trim());
              else if (hits[0]?.path) pick(hits[0].path);
            }}
          />
          {hits.filter((h) => h.path).map((h) => (
            <button key={h.id} type="button" className="menu-item composer-mono" onClick={() => pick(h.path!)}>{h.path}</button>
          ))}
        </div>
      </Popover>
    </>
  );
}

/** The attach box: focused a frame after the popover has placed itself (a hidden element takes no focus). */
function SearchBox(props: InputHTMLAttributes<HTMLInputElement>) {
  const ref = useFocusSoon<HTMLInputElement>();
  return <input ref={ref} {...props} />;
}
