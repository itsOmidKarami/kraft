import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Inspector } from "../graph/Inspector";
import { useRoving } from "../graph/useRoving";
import { request } from "../http";
import { Button } from "../ui/Button";
import { Popover } from "../ui/Popover";
import { AreaFrame } from "./draft/AreaFrame";
import { useConfigDraft, type ConfigDraft } from "./draft/useConfigDraft";
import type { Problem } from "./draft/types";
import { Kv, Note } from "./panes/controls";
import { ConnectForm } from "./repos/ConnectForm";
import { testsCell, testsTitle } from "./repos/evidence";
import { RepoConfig } from "./repos/RepoConfig";
import { problemsOf, repoName, reposOf, runningOf, type RepoView } from "./repos/types";
import { useRepoFragment } from "./repos/useRepoFragment";
import "./repos/repos.css";

export const reposUrl = (repo?: string) => `/templates/repos${repo ? `/${encodeURIComponent(repo)}` : ""}`;

/** The Repos area (Decisions §12's sibling, AreaRepos): `repos.yaml` as a table and a
 *  pane, edited through the `repos` draft and published with Review & publish. */
export function ReposPage() {
  const draft = useConfigDraft("repos", "repos");
  if (draft.status === "notFound" || (draft.status === "error" && !draft.view))
    return <div className="tpl-note" role="alert"><h1>Could not load repos</h1><Button onClick={() => void draft.reload()}>Retry</Button></div>;
  if (!draft.view) return <div className="tpl-note rp-loading" aria-busy="true"><div className="rp-skel" /><div className="rp-skel" /><div className="rp-skel" /></div>;
  return <Editor draft={draft} />;
}

// `impact.running` counts open items (not ended), as the board's groups do not: say "open".
const STATE_WORD = (r: RepoView, open: number) => `${r.entry.enabled === false ? "disabled" : "enabled"}${open ? ` · ${open} open` : ""}`;

function Editor({ draft }: { draft: ConfigDraft }) {
  const { repo: param } = useParams();
  const navigate = useNavigate();
  const view = draft.view!;
  const r = view.result;
  const data = reposOf(r);
  const running = runningOf(r);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("config");
  const [paneOpen, setPaneOpen] = useState(!!param);
  const [connecting, setConnecting] = useState(false);
  const connectBtn = useRef<HTMLButtonElement>(null);
  const [chains, setChains] = useState<string[]>([]);
  useEffect(() => {
    void request<{ id: string }[]>("/templates/chains").then((a) => {
      if (a.status === 200 && Array.isArray(a.body)) setChains(a.body.map((c) => c.id));
    });
  }, []);

  const repos = data?.repos ?? [];
  const sel = repos.find((x) => x.path === param || x.name === param) ?? (param ? undefined : repos[0]);
  const matches = useMemo(() => repos.filter((x) => !q.trim() || `${repoName(x)} ${x.path}`.toLowerCase().includes(q.trim().toLowerCase())), [repos, q]);
  const rove = useRoving(sel?.path, matches[0]?.path);
  const change = (path: string) => r.changes.find((c) => c.path === path);
  const select = (x: RepoView, to = tab) => {
    setTab(to);
    setPaneOpen(true);
    navigate(reposUrl(x.name && repos.filter((y) => y.name === x.name).length === 1 ? x.name : x.path));
  };
  const onFix = (p: Problem) => {
    const x = repos.find((y) => y.path === p.repo);
    if (x) select(x, "config");
  };

  const onKey = (e: React.KeyboardEvent) => {
    const at = matches.findIndex((x) => x.path === rove.active);
    const next = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: matches.length - 1 }[e.key];
    if (next === undefined || !matches.length) return;
    e.preventDefault();
    rove.go(matches[Math.max(0, Math.min(matches.length - 1, next))].path);
  };

  const area = useMemo(() => ({
    crumb: "Repos",
    files: ["repos.yaml"],
    toast: "Published repos · applies to items filed from now",
    affects: (res: typeof r) => {
      const n = Object.values(runningOf(res)).reduce((a, b) => a + b, 0);
      return (
        <>
          <Kv k="new items" v="use the published values from now on" />
          <Kv k="open" v={`${n} ${n === 1 ? "item keeps" : "items keep"} the values they started with`} />
        </>
      );
    },
  }), []);

  return (
    <AreaFrame
      draft={draft}
      area={area}
      pageKey="repos"
      title="Repos"
      onFix={onFix}
      onHighlight={(path) => { const x = repos.find((y) => y.path === path); if (x) select(x); }}
    >
      {({ size, review, reserve }) => (
        <>
          <div className="rp-body" style={{ right: reserve(paneOpen && !!sel) }}>
            {!data ? (
              <div className="tpl-note" role="alert">
                <h1>repos.yaml does not load</h1>
                {r.problems[0] && <p>{r.problems[0].message}</p>}
                <p>Open YAML in the header to repair it.</p>
              </div>
            ) : (
              <>
                <div className="rp-bar">
                  <input className="rp-search" type="search" aria-label="Search repos" placeholder="Search repos" value={q} onChange={(e) => setQ(e.target.value)} />
                  <button ref={connectBtn} type="button" className="btn btn-secondary" onClick={() => setConnecting((c) => !c)} aria-haspopup="dialog" aria-expanded={connecting}><Plus size={13} aria-hidden /> Connect repo</button>
                  <Popover anchor={connectBtn} open={connecting} onClose={() => setConnecting(false)} role="dialog" label="Connect a repo">
                    <ConnectForm
                      draft={draft}
                      known={new Set([...repos, ...data.detected].map((x) => x.path))}
                      entries={Object.fromEntries([...repos, ...data.detected].map((x) => [x.path, x.entry]))}
                      chains={chains}
                      onDone={(path) => {
                        setConnecting(false);
                        const x = path;
                        navigate(reposUrl(x));
                        setPaneOpen(true);
                        setTab("config");
                      }}
                    />
                  </Popover>
                </div>
                <div className="rp-head" aria-hidden>
                  <span>Repo</span><span>Chain</span><span>Steering</span><span>Tests</span><span>State</span>
                </div>
                <div className="rp-rows" role="listbox" aria-label="Repos" onKeyDown={onKey}>
                  {matches.map((x) => {
                    const c = change(x.path);
                    const bad = problemsOf(r, x.path).length > 0;
                    const steer = x.resolved.steering.join(", ");
                    const n = running[x.path] ?? 0;
                    const state = STATE_WORD(x, n);
                    return (
                      <div
                        key={x.path}
                        ref={rove.ref(x.path)}
                        role="option"
                        aria-selected={sel?.path === x.path}
                        aria-label={`${repoName(x)}, chain ${String(x.entry.default_chain_template ?? "default")}, ${state}${bad ? ", has a problem" : ""}`}
                        tabIndex={rove.tabIndex(x.path)}
                        className={`rp-row${sel?.path === x.path ? " is-sel" : ""}${x.entry.enabled === false ? " is-off" : ""}`}
                        onClick={() => select(x)}
                        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(x); } }}
                      >
                        <span className="rp-name">
                          {c && <span className={`rp-mark is-${c.kind}`} aria-hidden>{c.kind === "add" ? "+" : "~"}</span>}
                          <span className="rp-name-text" data-allow-ellipsis title={x.path}>{repoName(x)}</span>
                          {bad && <span className="rp-dot" role="img" aria-label="has a problem" />}
                        </span>
                        <span className="rp-cell">{String(x.entry.default_chain_template ?? "default")}</span>
                        <span className="rp-cell rp-cut" data-allow-ellipsis title={steer || undefined}>{steer || "—"}</span>
                        <span className="rp-cell rp-cut" data-allow-ellipsis title={testsTitle(x.entry)}>{testsCell(x.entry)}</span>
                        <span className={`rp-cell rp-state${x.entry.enabled === false ? "" : " is-on"}`}>{state}</span>
                      </div>
                    );
                  })}
                  {!matches.length && <p className="rp-empty">{repos.length ? "No repo matches." : "No repos connected yet. Connect one to start."}</p>}
                </div>
                {data.detected.length > 0 && !q.trim() && (
                  <section className="rp-detected" aria-label="Detected, not connected">
                    <h2 className="rp-h2">Detected · not connected</h2>
                    {data.detected.map((x) => (
                      <div key={x.path} className="rp-det">
                        <span className="rp-name-text" title={x.path}>{repoName(x)}</span>
                        <span className="rp-det-note">workspace member</span>
                        <Button onClick={() => void draft.ops([{ op: "connect_detected", path: x.path }]).then((a) => { if (a.status === 200) navigate(reposUrl(x.name ?? x.path)); })}>Connect</Button>
                      </div>
                    ))}
                  </section>
                )}
              </>
            )}
          </div>
          {!review && data && sel && (
            <RepoPane
              key={sel.path}
              draft={draft}
              repo={sel}
              running={running[sel.path] ?? 0}
              chains={chains}
              tab={tab}
              onTab={setTab}
              open={paneOpen}
              size={size}
              onOpen={setPaneOpen}
              onGone={(next) => navigate(reposUrl(next))}
              neighbour={matches.find((x) => x.path !== sel.path)}
            />
          )}
        </>
      )}
    </AreaFrame>
  );
}

function RepoPane({ draft, repo, running, chains, tab, onTab, open, size, onOpen, onGone, neighbour }: {
  draft: ConfigDraft;
  repo: RepoView;
  running: number;
  chains: string[];
  tab: string;
  onTab: (t: string) => void;
  open: boolean;
  size: ReturnType<typeof import("../graph/useResizable").useResizable>;
  onOpen: (o: boolean) => void;
  onGone: (next?: string) => void;
  neighbour?: RepoView;
}) {
  const r = draft.view!.result;
  const [note, setNote] = useState<string | null>(null);
  const fragment = useRepoFragment(draft, repo.path, tab === "yaml");
  const forge = typeof repo.entry.forge === "string" ? repo.entry.forge : null;
  const own = problemsOf(r, repo.path);
  const enabled = repo.entry.enabled !== false;
  const name = repoName(repo);

  const toggle = () => void draft.ops([{ op: "set_repo", path: repo.path, patch: { enabled: !enabled } }]);
  const disconnect = () => {
    // The server's refusal in the same words (`DELETE /repos`, the repos draft).
    if (running > 0) return setNote(`${name} has ${running} open ${running === 1 ? "item" : "items"}; finish or cancel ${running === 1 ? "it" : "them"} first. Disconnect is refused until then.`);
    setNote(null);
    void draft.ops([{ op: "remove_repo", path: repo.path }]).then((a) => { if (a.status === 200) onGone(neighbour ? neighbour.name ?? neighbour.path : undefined); });
  };

  return (
    <Inspector
      id="repos-pane"
      open={open}
      size={size}
      crumbs={[{ label: "Repos" }]}
      icon="git-branch"
      title={name}
      sub={`${repo.path}${forge ? ` · ${forge}` : ""} · ${running} open`}
      prob={own[0] ? { msg: own[0].message } : undefined}
      tabs={[{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }, { value: "yaml", label: "YAML" }]}
      tab={tab}
      onTab={onTab}
      onCollapse={() => onOpen(false)}
      onExpand={() => onOpen(true)}
      footer={
        <>
          <Button onClick={toggle}>{enabled ? "Disable" : "Enable"}</Button>
          <span className="bp-gap" />
          <Button variant="danger" onClick={disconnect}>Disconnect</Button>
        </>
      }
    >
      {note && <p className="rp-note" role="alert">{note}</p>}
      {own.filter((p) => !p.field).map((p, i) => <Note key={i} bad>{p.message}</Note>)}
      {tab === "overview" && (
        <>
          <Kv k="path" v={repo.path} mono />
          <Kv k="default chain" v={String(repo.entry.default_chain_template ?? "default")} mono />
          <Kv k="open items" v={String(running)} />
          <Kv k="state" v={enabled ? "enabled" : "disabled"} />
          <Kv k="steering" v={repo.resolved.steering.join(", ") || "—"} mono muted={!repo.resolved.steering.length} />
          <Kv k="steering from" v={repo.sources.steering === "repo" ? "this repo" : "the chain's library"} />
          <Kv k="forge" v={forge ? `${forge}${typeof repo.entry.project === "string" ? ` · ${repo.entry.project}` : ""}` : "—"} muted={!forge} />
        </>
      )}
      {tab === "config" && <RepoConfig draft={draft} repo={repo} chains={chains} />}
      {tab === "yaml" && (fragment.text === null
        ? <Note>{fragment.error ?? "Loading…"}</Note>
        : <><pre className="rp-yaml">{fragment.text}</pre><Note>Read-only. Edit the whole file with YAML in the header.</Note></>)}
    </Inspector>
  );
}
