import { Inbox, Layers, Rows3, Server, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { capKey, KEYS } from "./keys";
import { PolicyCell } from "./PolicyCell";
import { problemAt, type Ctx } from "./ctx";
import { CAP_LABEL, LEVEL_LABEL, LEVELS, type Below } from "./types";
import { show } from "./units";

const ICON: Record<string, ReactNode> = {
  instance: <Server size={14} aria-hidden />, work_item: <Inbox size={14} aria-hidden />, nodes: <Layers size={14} aria-hidden />, steps: <Rows3 size={14} aria-hidden />, tasks: <Sparkles size={14} aria-hidden />,
};

/** "implementer · library" or "verification.main · chain default": what sets a cap below policy. */
const belowText = (b: Below, unit: "min" | "usd" | "tok") =>
  `${b.via ? b.via.split(".").pop() : b.path.split(".").pop() || b.chain} ${show(unit, b.value)} · ${b.via ? "library" : `chain ${b.chain}`}`;

function Card({ id, name, sub, count, dashed, children }: { id: string; name: string; sub: string; count: number; dashed?: boolean; children: ReactNode }) {
  return (
    <section className={`pol-card${dashed ? " is-dashed" : ""}${count ? " is-bad" : ""}`} aria-label={name} data-scope={id}>
      <header className="pol-card-head">
        <span className="pol-card-ico">{ICON[id]}</span>
        <span className="pol-card-name">{name}</span>
        <span className="pol-card-sub">{sub}</span>
        {count > 0 && <span className="pol-card-count">{count} {count === 1 ? "problem" : "problems"}</span>}
      </header>
      {children}
    </section>
  );
}

/** Limits (Decisions §12): time and spend caps by scope, each scope a box inside the one it can't
 *  exceed (instance ⊃ work item ⊃ nodes ⊃ steps ⊃ tasks), under the page's heading with Preview on a chain. */
export function Limits({ ctx, preview }: { ctx: Ctx; preview?: ReactNode }) {
  const { p, changes } = ctx;
  const caps = p.limits.caps;
  const loose = ctx.problems.filter((x) => x.scope === "limits" && !x.level);
  const daily = problemAt(ctx, KEYS.daily.key);
  const levels = LEVELS.filter((l) => caps[l]);
  const scope = (i: number): ReactNode => {
    const level = levels[i];
    if (!level) return null;
    const probs = ctx.problems.filter((x) => x.level === level);
    const showBelow = Object.values(caps[level]).some((c) => Array.isArray(c.below));
    return (
      <Card key={level} id={level} name={LEVEL_LABEL[level].name} sub={LEVEL_LABEL[level].sub} count={probs.length}>
        <div className={`pol-grid${showBelow ? " has-below" : ""}`}>
          <span /><span className="pol-colh">default</span><span className="pol-colh">maximum</span>{showBelow && <span className="pol-colh">set below policy</span>}
          {Object.keys(caps[level]).map((cap) => {
            const c = caps[level][cap];
            const label = CAP_LABEL[cap] ?? { name: cap, unit: "min" as const };
            const dk = capKey("default", level, cap), mk = capKey("maximum", level, cap);
            const bad = problemAt(ctx, dk.key) || problemAt(ctx, mk.key);
            return (
              <div key={cap} className={`pol-row${bad ? " is-bad" : ""}`} role="group" aria-label={`${LEVEL_LABEL[level].name} ${label.name}`}>
                <span className="pol-k">{label.name}</span>
                <PolicyCell draft={ctx.draft} k={dk} label={`${LEVEL_LABEL[level].name} ${label.name} default`} value={c.default.value} change={changes.get(dk.key)} problem={problemAt(ctx, dk.key)} />
                <PolicyCell draft={ctx.draft} k={mk} label={`${LEVEL_LABEL[level].name} ${label.name} maximum`} value={c.maximum.value} bound change={changes.get(mk.key)} problem={problemAt(ctx, mk.key)} inherited={c.maximum.source && c.maximum.source !== level ? c.maximum.source : null} />
                {showBelow && (
                  <span className="pol-below">
                    {(c.below ?? []).map((b, k) => <span key={k} className={b.exceeds ? "is-bad" : undefined}>{belowText(b, label.unit as "min")}{b.exceeds ? " !" : ""}</span>)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
        {level === "work_item" && (
          <div className="pol-one">
            <span className="pol-k">outer ceiling</span>
            <PolicyCell draft={ctx.draft} k={KEYS.outer} label="outer ceiling" value={outerValue(ctx)} change={changes.get(KEYS.outer.key)} problem={problemAt(ctx, KEYS.outer.key)} />
            <span className="pol-help">A second dollar cap on each item. Both bind; the lower one wins{p.limits.work_item_usd.binding ? `: ${p.limits.work_item_usd.binding.key} ${show("usd", p.limits.work_item_usd.binding.value)}` : ""}.</span>
          </div>
        )}
        {level === "tasks" && <p className="pol-note">A wait&apos;s timeout is its task&apos;s wall clock, so the tasks wall-clock maximum is also the longest any wait may wait.</p>}
        {probs.map((x, k) => <p key={k} className="pol-note is-bad">! {x.message}</p>)}
        {scope(i + 1)}
      </Card>
    );
  };
  return (
    <>
      <header className="pol-head">
        <h2 className="pol-title">Limits</h2>
        <p className="pol-intro">Time and spend caps per scope. Each scope sits inside the one it can&apos;t exceed. An unset maximum is no bound.</p>
        {preview}
      </header>
      {loose.map((x, i) => <p key={i} className="pol-note is-bad">! {x.message}</p>)}
      <Card id="instance" name="instance" sub="every item on this install" count={0} dashed>
        <div className="pol-one">
          <span className="pol-k">per day</span>
          <PolicyCell draft={ctx.draft} k={KEYS.daily} label="per day" value={(p.limits.daily_usd.value as number | null) ?? null} change={changes.get(KEYS.daily.key)} problem={daily} />
          <span className="pol-help">Dollars across every item since local midnight. Refuses the next agent task; can&apos;t stop one already running.</span>
        </div>
        {scope(0)}
      </Card>
    </>
  );
}

/** `budget.work_item_usd` as the draft holds it: the model's own value, not a derived one. */
function outerValue(ctx: Ctx): number | null {
  const m = ctx.draft.view!.result.model["policy.yaml"] as { budget?: { work_item_usd?: number | null } } | undefined;
  return m?.budget?.work_item_usd ?? null;
}
