import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Plus } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { repoName } from "../../../format";
import { showToast } from "../../ui/Toast";
import { useStore } from "../../../store";
import type { WorkItem } from "../../../types";
import { act } from "../../item/actions";
import { groupOf, groupsOf, type GroupKey } from "../../board/model";
import { gateWords, nodeWords } from "../../board/rowText";
import { useBoardPrefs } from "../../board/prefs";
import { ChoiceSheet, ConfirmSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { itemPath, needsDocument, reviewPath, type CardButton } from "./actions";
import { Card } from "./Card";
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
  /** The card whose Approve is being confirmed: a gate passed is not taken back, so one tap only asks. */
  const [approving, setApproving] = useState<{ item: WorkItem; gate: string } | null>(null);

  useEffect(() => {
    api.getRepos().then((r) => setNoRepos(r.repos.length === 0)).catch(() => {});
  }, []);

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };

  const inRepo = (i: WorkItem) => !repo || i.repo === repo;
  const scoped = items.filter(inRepo);
  const count = (g: GroupKey) => scoped.filter((i) => groupOf(i) === g).length;
  const counts: Record<Chip, number> = { all: scoped.length, needs: count("needs"), running: count("running"), not_started: count("not_started"), done: count("done") };

  const groups = useMemo(
    () => groupsOf(items, { filter: { q: "", repo, chain: "" }, group: "status", sort: "attention", doneCap: showAll ? null : (prefs?.show_done ?? 5) }),
    [items, repo, showAll, prefs?.show_done],
  );
  const shown = groups.filter((g) => (f === "all" ? g.total > 0 : g.key === f));
  const repos = [...new Set(items.map((i) => i.repo))].sort((a, b) => repoName(a).localeCompare(repoName(b)));

  const fail = (id: string, message: string | null) =>
    setErrors((e) => {
      const { [id]: _, ...rest } = e;
      return message ? { ...rest, [id]: message } : rest;
    });

  async function onButton(item: WorkItem, b: CardButton) {
    if (b.kind === "reject") return navigate(itemPath(item.id, "?compose=reject"));
    if (b.kind === "answer") return navigate(itemPath(item.id, "?compose=answer"));
    if (b.kind === "open") return navigate(itemPath(item.id));
    if (b.kind === "raise") return navigate(itemPath(item.id, "?raise=1"));
    fail(item.id, null);
    if (b.kind === "approve") {
      setApproving({ item, gate: b.gate });
      return sheet.open("approve");
    }
    setBusy(item.id);
    const r = await act.resume(item.id);
    setBusy(null);
    if (r.ok) return void showToast("Resumed.");
    fail(item.id, r.error);
  }

  // Confirmed: approve, and on a refusal leave the sheet for the card's own line, or for the review when the document must be read first.
  async function approve({ item, gate }: { item: WorkItem; gate: string }) {
    setBusy(item.id);
    const r = await act.approve(item.id, gate);
    setBusy(null);
    if (r.ok) {
      sheet.close();
      return void showToast(`Approved ${gateWords(gate)}.`);
    }
    if (needsDocument(r.error)) {
      showToast(r.error);
      return sheet.closeThen(() => navigate(reviewPath(item.id, gate)));
    }
    sheet.close();
    fail(item.id, r.error);
  }
  const nextAfter = (item: WorkItem, gate: string) => {
    const nodes = item.chain_definition?.nodes ?? [];
    return nodes[nodes.findIndex((n) => n.id === gate) + 1]?.id ?? null;
  };

  const empty = items.length === 0;
  return (
    <>
      <RootHeader title="Board">
        {offline && <span className="ph-offline">offline</span>}
        <button type="button" className="ph-pill" onClick={() => sheet.open("repo")}>
          {repo ? repoName(repo) : "All repos"}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        <span className="ph-spacer" />
        <Link to="/work-items/new" className="ph-fab" aria-label="New work item"><Plus size={20} aria-hidden="true" /></Link>
      </RootHeader>
      <div className="ph-chips" role="group" aria-label="Show">
        {CHIPS.map((c) => (
          <button key={c.f} type="button" className={`ph-chip${f === c.f ? " ph-is-on" : ""}`} aria-pressed={f === c.f} onClick={() => set({ f: c.f === "all" ? "" : c.f })}>
            {c.label}<span className="ph-chip-n">{counts[c.f]}</span>
          </button>
        ))}
      </div>
      <div className="ph-content ph-board">
        {olderServer(health) && <p className="ph-restart" role="alert">{OLDER_SERVER} Run <code>kraft admin restart</code>.</p>}
        {/* An update or a rollback installed under this server, in a tab opened before it: the sidebar's line, which the phone has no sidebar for (R11b-08). */}
        {!olderServer(health) && restartWords(health) && <p className="ph-restart" role="status">Kraft {restartWords(health)}.</p>}
        {load.state === "loading" && empty && <div className="ph-skeleton" aria-hidden="true"><span /><span /><span /></div>}
        {empty && load.state !== "loading" && (
          <p className="ph-empty">
            {noRepos ? <>No repository is connected. Run <code>kraft repo connect</code> in a repo on the machine, or connect one from a computer.</> : "Nothing here. Tap + to file one."}
          </p>
        )}
        {!empty && shown.length === 0 && <p className="ph-empty">Nothing here for this filter.</p>}
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
      {sheet.is("approve") && approving && (
        <ConfirmSheet
          // The card's own words: its tail reads "approve spec" for spec_approval.
          title={`Approve ${gateWords(approving.gate)}?`}
          text={`${approving.item.title} ${nextAfter(approving.item, approving.gate) ? `moves on to ${nodeWords(nextAfter(approving.item, approving.gate)!)}` : "moves on"} at once. An approval is not taken back; to read first, open the review.`}
          busy={busy === approving.item.id}
          confirm={{ label: "Approve", run: () => void approve(approving) }}
          onClose={sheet.close}
        >
          <button type="button" className="ph-linkbtn" onClick={() => sheet.goTo(reviewPath(approving.item.id, approving.gate))}>Open the review</button>
        </ConfirmSheet>
      )}
      {sheet.is("repo") && (
        <ChoiceSheet
          title="Repo"
          value={repo}
          options={[{ value: "", label: "All repos", hint: String(items.length) }, ...repos.map((r) => ({ value: r, label: repoName(r), hint: String(items.filter((i) => i.repo === r).length) }))]}
          onPick={(v) => sheet.closeThen(() => set({ repo: v }))}
          onClose={sheet.close}
        />
      )}
    </>
  );
}
