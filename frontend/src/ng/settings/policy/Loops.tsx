import { detailOf } from "../../http";
import { ValueCell } from "../../templates/draft/ValueCell";
import { KEYS, SEVERITIES, SEVERITY_KEY, STUCK_KEY } from "./keys";
import { PolicyCell, setValue, was } from "./PolicyCell";
import { problemAt, type Ctx } from "./ctx";
import { parse, raw, show } from "./units";

const SOURCE: Record<string, string> = { loop: "the loop's own", node: "its node", chain: "its chain", defaults: "defaults:", maxima: "maxima:", loops: "loops:", default: "default:" };

/** Loops (Decisions §12): fix-loop defaults and maxima, which findings start another cycle, and what happens when an item is stuck. */
export function Loops({ ctx }: { ctx: Ctx }) {
  const { p, draft, changes } = ctx;
  const live = p.loops.live;
  const stray = p.loops.entries.filter((e) => !e.live);
  const used = (pick: (l: (typeof live)[number]) => string) => live.map(pick).join(", ");
  const sev = p.findings.loop_severities.value ?? [];
  const sevChange = changes.get(SEVERITY_KEY.key);
  const stuck = p.escalation.auto_escalate_stuck?.value !== false;
  const entry = (key: string) => p.loops.entries.find((e) => e.key === key);

  const setLoop = (key: string, field: "max_attempts" | "wall_clock_s", text: string, unit: "count" | "s-as-min") => {
    const v = parse(unit, text);
    if ("error" in v) return v.error;
    if (v.value === null) return "A loop's own value cannot be blank: remove its entry instead.";
    return draft.ops([{ op: "set_loop", key, [field]: v.value }], { quiet: true }).then((a) => (a.status === 200 ? null : detailOf(a.body)));
  };
  const toggleSeverity = (s: string) => {
    const next = SEVERITIES.filter((x) => (x === s ? !sev.includes(x) : sev.includes(x)));
    void setValue(draft, SEVERITY_KEY, next);
  };

  return (
    <>
      <p className="pol-intro">Defaults for every fix loop that doesn&apos;t set its own, which findings start another cycle, and what happens when an item stops making progress.</p>
      <section className="pol-card" aria-label="Fix loops">
        <div className="pol-grid has-used">
          <span /><span className="pol-colh">default</span><span className="pol-colh">maximum</span><span className="pol-colh">used by</span>
          <div className="pol-row" role="group" aria-label="Attempts">
            <span className="pol-k">attempts</span>
            <PolicyCell draft={draft} k={KEYS.attemptsDefault} label="attempts default" value={p.limits.max_attempts.default.value} change={changes.get(KEYS.attemptsDefault.key)} problem={problemAt(ctx, KEYS.attemptsDefault.key)} />
            <PolicyCell draft={draft} k={KEYS.attemptsMax} label="attempts maximum" value={p.limits.max_attempts.maximum.value} bound change={changes.get(KEYS.attemptsMax.key)} problem={problemAt(ctx, KEYS.attemptsMax.key)} />
            <span className="pol-help">{live.length ? `${used((l) => `${l.node} ${l.attempts.value}`)} · a chain, node or loop may set its own` : "no fix loop in any chain"}</span>
          </div>
          <div className="pol-row" role="group" aria-label="Wall clock">
            <span className="pol-k">wall clock</span>
            <PolicyCell draft={draft} k={KEYS.wallDefault} label="wall clock default" value={p.limits.timeout_minutes.default.value} change={changes.get(KEYS.wallDefault.key)} problem={problemAt(ctx, KEYS.wallDefault.key)} />
            <PolicyCell draft={draft} k={KEYS.wallMax} label="wall clock maximum" value={p.limits.timeout_minutes.maximum.value} bound change={changes.get(KEYS.wallMax.key)} problem={problemAt(ctx, KEYS.wallMax.key)} />
            <span className="pol-help">{live.length ? `all ${live.length} ${live.length === 1 ? "loop" : "loops"} in published chains · a chain or node may set its own` : "—"}</span>
          </div>
        </div>
      </section>

      <section className="pol-card" aria-label="Every fix loop">
        <h2 className="pol-h2">Every fix loop</h2>
        <div className="pol-loops">
          <div className="pol-loop is-default" role="group" aria-label="Loops without their own entry">
            <span className="pol-k">every loop without its own entry</span>
            <PolicyCell draft={draft} k={KEYS.loopAttempts} label="default loop attempts" value={p.loops.default.attempts} change={changes.get(KEYS.loopAttempts.key)} problem={problemAt(ctx, KEYS.loopAttempts.key)} />
            <PolicyCell draft={draft} k={KEYS.loopWall} label="default loop wall clock" value={p.loops.default.wall_clock_s} change={changes.get(KEYS.loopWall.key)} problem={problemAt(ctx, KEYS.loopWall.key)} />
            <span />
          </div>
          {live.map((l) => (
            <div key={l.key} className="pol-loop" role="group" aria-label={`Loop ${l.key}`}>
              <span className="pol-k" title={l.key}>{l.node} <span className="pol-chain">{l.chain}</span></span>
              <span className="pol-cell">
                <ValueCell label={`${l.node} attempts`} value={raw("count", l.attempts.value)} display={show("count", l.attempts.value)} onCommit={(t) => setLoop(l.key, "max_attempts", t, "count")} />
                <span className="pol-from">{SOURCE[l.attempts.source ?? ""] ?? l.attempts.source}</span>
              </span>
              <span className="pol-cell">
                <ValueCell label={`${l.node} wall clock`} value={raw("s-as-min", l.wall_clock_s.value)} display={show("s-as-min", l.wall_clock_s.value)} onCommit={(t) => setLoop(l.key, "wall_clock_s", t, "s-as-min")} />
                <span className="pol-from">{SOURCE[l.wall_clock_s.source ?? ""] ?? l.wall_clock_s.source}</span>
              </span>
              {entry(l.key)
                ? <button type="button" className="pol-link" aria-label={`Remove the loops entry for ${l.node}`} onClick={() => void draft.ops([{ op: "remove_loop", key: l.key }])}>Remove entry</button>
                : <span />}
            </div>
          ))}
        </div>
        {stray.length > 0 && (
          <>
            <h2 className="pol-h2">Not a loop in any chain</h2>
            <div className="pol-loops">
            {stray.map((e) => (
              <div key={e.key} className="pol-loop is-stray" role="group" aria-label={`Entry ${e.key}`}>
                <span className="pol-k is-bad">{e.key}</span>
                <span className="pol-help">{show("count", e.attempts)} attempts · {show("s-as-min", e.wall_clock_s)}</span>
                <span className="pol-help is-bad">{ctx.problems.find((x) => x.field === `loops.${e.key}` || x.path === `loops.${e.key}`)?.message ?? "names no fix loop in the library"}</span>
                <button type="button" className="pol-link" aria-label={`Remove the loops entry ${e.key}`} onClick={() => void draft.ops([{ op: "remove_loop", key: e.key }])}>Remove</button>
              </div>
            ))}
            </div>
          </>
        )}
      </section>

      <section className="pol-card" aria-label="Findings">
        <div className="pol-one">
          <span className="pol-k">burns a cycle</span>
          <span className="pol-chips" role="group" aria-label="Findings that burn a fix cycle">
            {SEVERITIES.map((s) => (
              <button key={s} type="button" className={`pol-chip${sev.includes(s) ? " is-on" : ""}${sevChange ? " is-changed" : ""}`} aria-pressed={sev.includes(s)} onClick={() => toggleSeverity(s)}>{s}</button>
            ))}
            {sevChange && <s className="pol-was">{was(sevChange)}</s>}
          </span>
          <span className="pol-help">Finding severities that start another fix cycle. The rest are shown at the gate without looping. Order: the loop, an item override, the node, the chain, then these. Repos may set their own findings.</span>
        </div>
      </section>

      <section className="pol-card" aria-label="When an item is stuck">
        <h2 className="pol-h2">When an item is stuck</h2>
        <div className="pol-one">
          <span className="pol-k">auto-escalate</span>
          <span className="pol-chips" role="group" aria-label="Auto-escalate on stuck">
            {[true, false].map((on) => (
              <button key={String(on)} type="button" className={`pol-chip${stuck === on ? " is-on" : ""}${changes.has(STUCK_KEY.key) ? " is-changed" : ""}`} aria-pressed={stuck === on} onClick={() => void setValue(draft, STUCK_KEY, on)}>{on ? "on" : "off"}</button>
            ))}
          </span>
          <span className="pol-help">{stuck ? "An item that reports no progress escalates to a person on its own. A chain or an item override can turn this off or retune it for one node." : "Off: a stuck item waits until a person escalates it."}</span>
        </div>
        {stuck && (
          <>
            <div className="pol-one">
              <span className="pol-k">max per item</span>
              <PolicyCell draft={draft} k={KEYS.stuckCap} label="max auto-escalations per item" value={(p.escalation.auto_escalate_stuck_cap?.value as number | null) ?? null} change={changes.get(KEYS.stuckCap.key)} problem={problemAt(ctx, KEYS.stuckCap.key)} />
              <span className="pol-help">How many times one item escalates on its own before it waits for a person.</span>
            </div>
            <div className="pol-one">
              <span className="pol-k">delay</span>
              <PolicyCell draft={draft} k={KEYS.stuckDelay} label="delay before escalating" value={(p.escalation.auto_escalate_delay_s?.value as number | null) ?? null} change={changes.get(KEYS.stuckDelay.key)} problem={problemAt(ctx, KEYS.stuckDelay.key)} />
              <span className="pol-help">How long a stuck item waits before escalating. 0 escalates at once.</span>
            </div>
            {p.escalation.auto_review_attempts && (
              <div className="pol-one">
                <span className="pol-k">review attempts</span>
                <PolicyCell draft={draft} k={KEYS.reviewAttempts} label="auto-review attempts" value={(p.escalation.auto_review_attempts.value as number | null) ?? null} change={changes.get(KEYS.reviewAttempts.key)} problem={problemAt(ctx, KEYS.reviewAttempts.key)} />
                <span className="pol-help">How many times an agent review may ask for changes before a person is asked.</span>
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}
