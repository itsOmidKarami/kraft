import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Plus } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { repoName } from "../../../format";
import { showToast } from "../../ui/Toast";
import { tip } from "../../ui/Tooltip";
import { useStore } from "../../../store";
import type { WorkItem } from "../../../types";
import { act } from "../../item/actions";
import { chainOf, groupOf, groupsOf, matches, type GroupKey } from "../../board/model";
import { ALL_GATES, ALL_STOPS, gateChip, nodeWords, stopChip, stopFilterOptions, stopWords } from "../../board/rowText";
import { useBoardPrefs } from "../../board/prefs";
import { ChoiceSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { itemPath, reviewPath, type CardButton } from "./actions";
import { Card } from "./Card";
import { NoMatch } from "../../ui/NoMatch";
import { useListLoad, useNow } from "./useListLoad";
import { OLDER_SERVER, olderServer, restartWords, useHealth } from "../../shell/health";
import "./board.css";

const CHIPS = [
  { f: "all", label: "All" },
  { f: "needs", label: "Needs you" },
  { f: "running", label: "Running" },
  // The desktop's fourth group: without it the chips never added up to All (R8b-12).
  { f: "not_started", label: "Not started" },
  { f: "done", label: "Done" },
] as const;
type Chip = (typeof CHIPS)[number]["f"];
const isChip = (v: string | null): v is Chip => CHIPS.some((c) => c.f === v);

/** `/`: the board as cards (W17 brief B). The filter and repo are in the URL so
 *  they survive Back; the groups are the desktop's four (R54). */
export function Board() {
  const [params, setParams] = useSearchParams();
  const f: Chip = isChip(params.get("f")) ? (params.get("f") as Chip) : "all";
  const repo = params.get("repo") ?? "";
  const chain = params.get("chain") ?? "";
  const gate = params.get("gate") ?? "";
  const stop = params.get("stop") ?? "";
  const navigate = useNavigate();
  const sheet = useSheet();
  const now = useNow();
  const prefs = useBoardPrefs();
  const { load, offline } = useListLoad();
  // R10c-01: an older server still running after an update says so over the board.
  const health = useHealth();
  const byId = useStore((s) => s.workItems);
  const items = useMemo(() => Object.values(byId).filter((i) => i.display_status !== "archived"), [byId]);
  const [showAll, setShowAll] = useState(false);
  const [noRepos, setNoRepos] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    api.getRepos().then((r) => setNoRepos(r.repos.length === 0)).catch(() => {});
  }, []);

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };

  const scoped = items.filter((i) => matches(i, { q: "", repo, chain, gate, stop }));
  const count = (g: GroupKey) => scoped.filter((i) => groupOf(i) === g).length;
  const counts: Record<Chip, number> = { all: scoped.length, needs: count("needs"), running: count("running"), not_started: count("not_started"), done: count("done") };

  const groups = useMemo(
    () => groupsOf(items, { filter: { q: "", repo, chain, gate, stop }, group: "status", sort: "attention", doneCap: showAll ? null : (prefs?.show_done ?? 5) }),
    [items, repo, chain, gate, stop, showAll, prefs?.show_done],
  );
  const shown = groups.filter((g) => (f === "all" ? g.total > 0 : g.key === f));
  const repos = [...new Set(items.map((i) => i.repo))].sort((a, b) => repoName(a).localeCompare(repoName(b)));
  const chains = [...new Set(items.map(chainOf))].sort();
  const { gates, stops } = stopFilterOptions(items);
  // A chosen gate or stop nothing waits at any more stays listed, so the sheet says what is filtering.
  const gateLabel = gates.find((g) => g.value === gate)?.label ?? nodeWords(gate);
  const stopLabel = stops.find((s) => s.value === stop)?.label ?? stopWords(stop);

  const fail = (id: string, message: string | null) =>
    setErrors((e) => {
      const { [id]: _, ...rest } = e;
      return message ? { ...rest, [id]: message } : rest;
    });

  async function onButton(item: WorkItem, b: CardButton) {
    if (b.kind === "review") return navigate(reviewPath(item.id, b.gate));
    if (b.kind === "answer") return navigate(itemPath(item.id, "?compose=answer"));
    if (b.kind === "open") return navigate(itemPath(item.id));
    if (b.kind === "raise") return navigate(itemPath(item.id, "?raise=1"));
    fail(item.id, null);
    setBusy(item.id);
    const r = await act.resume(item.id);
    setBusy(null);
    if (r.ok) return void showToast("Resumed.");
    fail(item.id, r.error);
  }

  const empty = items.length === 0;
  return (
    <>
      <RootHeader title="Board">
        <button type="button" className="ph-pill" onClick={() => sheet.open("repo")}>
          {repo ? repoName(repo) : "All repos"}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        <span className="ph-spacer" />
        <Link to="/work-items/new" className="ph-fab" {...tip("New work item")}><Plus size={20} aria-hidden="true" /></Link>
      </RootHeader>
      <div className="ph-chips" role="group" aria-label="Show">
        {CHIPS.map((c) => (
          <button key={c.f} type="button" className={`ph-chip${f === c.f ? " ph-is-on" : ""}`} aria-pressed={f === c.f} onClick={() => set({ f: c.f === "all" ? "" : c.f })}>
            {c.label}<span className="ph-chip-n">{counts[c.f]}</span>
          </button>
        ))}
      </div>
      <div className="ph-chips ph-filters" role="group" aria-label="Filter">
        <button type="button" className={`ph-pill${chain ? " ph-is-set" : ""}`} onClick={() => sheet.open("chain")}>{chain ? `Chain · ${chain}` : "Chain"}<ChevronDown size={14} aria-hidden="true" /></button>
        <button type="button" className={`ph-pill${gate ? " ph-is-set" : ""}`} onClick={() => sheet.open("gate")}>{gate ? gateChip(gateLabel) : "Gate"}<ChevronDown size={14} aria-hidden="true" /></button>
        <button type="button" className={`ph-pill${stop ? " ph-is-set" : ""}`} onClick={() => sheet.open("stop")}>{stop ? stopChip(stopLabel) : "Stop reason"}<ChevronDown size={14} aria-hidden="true" /></button>
      </div>
      <div className="ph-content ph-board">
        {olderServer(health) && <p className="ph-restart" role="alert">{OLDER_SERVER} Run <code>kraft admin restart</code>.</p>}
        {/* An update or a rollback installed under this server, in a tab opened before it: the sidebar's line, which the phone has no sidebar for (R11b-08). */}
        {!olderServer(health) && restartWords(health) && <p className="ph-restart" role="status">Kraft {restartWords(health)}.</p>}
        {load.state === "loading" && empty && <div className="ph-skeleton" aria-hidden="true"><span /><span /><span /></div>}
        {empty && load.state !== "loading" && !offline && (
          <p className="ph-empty">
            {noRepos ? <>No repository is connected. Run <code>kraft repo connect</code> in a repo on the machine, or connect one from a computer.</> : "Nothing here. Tap + to file one."}
          </p>
        )}
        {!empty && shown.length === 0 && <NoMatch className="ph-empty" onClear={() => set({ f: "", repo: "", chain: "", gate: "", stop: "" })} />}
        {shown.map((g) => (
          <section key={g.key} className="ph-group" aria-label={g.label}>
            <h2 className={`ph-group-head${g.key === "needs" ? " ph-is-hot" : ""}`}>{g.label}<span>{g.total}</span></h2>
            {g.rows.map((i) => (
              <Card key={i.id} item={i} now={now} offline={offline} busy={busy === i.id} error={errors[i.id]} onOpen={() => navigate(itemPath(i.id))} onButton={(b) => onButton(i, b)} />
            ))}
            {g.done && g.rows.length < g.total && (
              <button type="button" className="ph-more-row" onClick={() => setShowAll(true)}>Show all {g.total}</button>
            )}
          </section>
        ))}
      </div>
      {sheet.is("repo") && (
        <ChoiceSheet
          title="Repo"
          value={repo}
          options={[{ value: "", label: "All repos", hint: String(items.length) }, ...repos.map((r) => ({ value: r, label: repoName(r), hint: String(items.filter((i) => i.repo === r).length) }))]}
          onPick={(v) => sheet.closeThen(() => set({ repo: v }))}
          onClose={sheet.close}
        />
      )}
      {sheet.is("chain") && (
        <ChoiceSheet
          title="Chain"
          value={chain}
          options={[{ value: "", label: "All chains", hint: String(items.length) }, ...chains.map((c) => ({ value: c, label: c, hint: String(items.filter((i) => chainOf(i) === c).length) }))]}
          onPick={(v) => sheet.closeThen(() => set({ chain: v }))}
          onClose={sheet.close}
        />
      )}
      {sheet.is("gate") && (
        <ChoiceSheet
          title="Gate"
          value={gate}
          options={[{ value: "", label: ALL_GATES }, ...gates.map((g) => ({ value: g.value, label: g.label, hint: String(g.n) })), ...(gate && !gates.some((g) => g.value === gate) ? [{ value: gate, label: gateLabel, hint: "0" }] : [])]}
          onPick={(v) => sheet.closeThen(() => set({ gate: v }))}
          onClose={sheet.close}
        />
      )}
      {sheet.is("stop") && (
        <ChoiceSheet
          title="Stop reason"
          value={stop}
          options={[{ value: "", label: ALL_STOPS }, ...stops.map((s) => ({ value: s.value, label: s.label, hint: String(s.n) })), ...(stop && !stops.some((s) => s.value === stop) ? [{ value: stop, label: stopLabel, hint: "0" }] : [])]}
          onPick={(v) => sheet.closeThen(() => set({ stop: v }))}
          onClose={sheet.close}
        />
      )}
    </>
  );
}
