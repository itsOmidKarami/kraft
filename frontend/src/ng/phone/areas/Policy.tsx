import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { detailOf, request } from "../../http";
import { problemAt, type Ctx } from "../../settings/policy/ctx";
import { capKey, KEYS, SEVERITIES, SEVERITY_KEY, STUCK_KEY, type KeySpec } from "../../settings/policy/keys";
import { SECTIONS, sectionOfProblem, type Section } from "../../settings/policy/sections";
import { CAP_LABEL, LEVEL_LABEL, LEVELS, policyOf } from "../../settings/policy/types";
import { parse, parseAge, parseSize, raw, show } from "../../settings/policy/units";
import type { Change } from "../../templates/draft/types";
import { useConfigDraft, type ConfigDraft } from "../../templates/draft/useConfigDraft";
import { problemText } from "../../templates/problems";
import { TabStrip } from "../ui/Rows";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type Edit, type RowSpec } from "./kit";

const isSection = (s: string | undefined): s is Section => SECTIONS.some((x) => x.id === s);
const was = (c: Change | undefined) => (c ? c.summary.split(" → ")[0] : undefined);
const UNIT_WORD = { usd: "dollars", tok: "tokens", min: "minutes", days: "days", s: "seconds", count: "a whole number", "s-as-min": "minutes", size: "a size: 1000M, 10G, 1T", age: "hours or days: 12h, 24h, 2d" } as const;

/** `set_value` as the desktop's `PolicyCell` sends it: a scope, the dotted key, the value (null clears). */
const sendValue = async (draft: ConfigDraft, k: Pick<KeySpec, "scope" | "key">, value: unknown) => {
  const a = await draft.ops([{ op: "set_value", scope: k.scope, key: k.key, value }], { quiet: true });
  return a.status === 200 ? null : detailOf(a.body);
};

/** `/settings/policy/:section`: Limits, Loops, Housekeeping as tabs of one screen (W17 brief N.1). Every number is the server's resolve; the phone computes no bound. */
export function PolicyScreen() {
  const { section } = useParams();
  const draft = useConfigDraft("policy", "policy");
  const navigate = useNavigate();
  const { edit, node } = useEditor();
  const [active, setActive] = useState<number | null>(null);
  const stamp = draft.view?.files["policy.yaml"];
  useEffect(() => {
    let live = true;
    void request<{ active_count?: number }>("/policy").then((a) => live && a.status === 200 && setActive(a.body.active_count ?? null));
    return () => void (live = false);
  }, [stamp]);
  if (!isSection(section)) return <Navigate to="/settings/policy/limits" replace />;

  const r = draft.view?.result;
  const p = r ? policyOf(r) : null;
  const changes = new Map((r?.changes ?? []).map((c) => [c.path, c]));
  const ctx: Ctx | null = p && r ? { draft, p, changes, problems: r.problems, active } : null;
  const here = (r?.problems ?? []).filter((x) => sectionOfProblem(x) === section);

  return (
    <AreaScreen title="Policy" sub={SECTIONS.find((s) => s.id === section)!.sub} draft={draft}>
      <TabStrip label="Policy section" value={section} onChange={(id) => navigate(`/settings/policy/${id}`, { replace: true })} tabs={SECTIONS.map((s) => ({ id: s.id, label: s.label }))} />
      {draft.status === "error" && !draft.view && <p className="ph-error" role="alert">The policy could not be read.</p>}
      {r && !ctx && <p className="ph-error" role="alert">policy.yaml does not load{r.problems[0] ? `: ${r.problems[0].message}` : ""}. Open YAML to repair it.</p>}
      {here.length > 0 && <Group title="Problems" rows={here.map((x, i): RowSpec => ({ key: `p${i}`, label: problemText(x), sub: x.field ?? x.path, chips: [{ label: "problem", tone: "bad" }] }))} />}
      {ctx && (section === "limits" ? <Limits ctx={ctx} edit={edit} /> : section === "loops" ? <Loops ctx={ctx} edit={edit} /> : <Housekeeping ctx={ctx} edit={edit} />)}
      {node}
    </AreaScreen>
  );
}

type Edits = { ctx: Ctx; edit: (e: Edit) => void };

const textEdit = (ctx: Ctx, k: KeySpec, label: string, value: number | null, bound?: boolean): Edit => ({
  kind: "text",
  title: label,
  help: `In ${UNIT_WORD[k.unit]}. ${bound ? "Leave empty for no bound." : "Leave empty to clear it."}`,
  value: raw(k.unit, value),
  set: async (t) => {
    const v = parse(k.unit, t, k.zero);
    return "error" in v ? v.error : sendValue(ctx.draft, k, v.value);
  },
});

/** One number: its value, the published one it replaced, and a problem the draft holds on it. */
function single({ ctx, edit }: Edits, k: KeySpec, label: string, value: number | null, o: { bound?: boolean; sub?: string; never?: string } = {}): RowSpec {
  const bad = problemAt(ctx, k.key);
  const ch = ctx.changes.get(k.key);
  return {
    key: k.key,
    label,
    value: value == null && o.never ? o.never : show(k.unit, value, o.bound),
    sub: bad ? bad.message : ch ? `was ${was(ch)}${o.sub ? ` · ${o.sub}` : ""}` : o.sub,
    changed: !!ch,
    chips: bad ? [{ label: "problem", tone: "bad" }] : undefined,
    onEdit: () => edit(textEdit(ctx, k, label, value, o.bound)),
  };
}

/** A storage size (`10G`) or clean-up age (`24h`): text, blank clears. */
function sizeRow({ ctx, edit }: Edits, k: KeySpec, label: string, value: string | null, unset: string, sub: string, parser = parseSize, help = "A size: 1000M, 10G, 1T. Leave empty to clear it."): RowSpec {
  const bad = problemAt(ctx, k.key);
  const ch = ctx.changes.get(k.key);
  return {
    key: k.key,
    label,
    value: value ?? unset,
    sub: bad ? bad.message : ch ? `was ${was(ch)} · ${sub}` : sub,
    changed: !!ch,
    chips: bad ? [{ label: "problem", tone: "bad" }] : undefined,
    onEdit: () => edit({
      kind: "text", title: label, help, value: value ?? "",
      set: async (t) => {
        const v = parser(t);
        return "error" in v ? v.error : sendValue(ctx.draft, k, v.value);
      },
    }),
  };
}

/** A default and a maximum on one row: the sheet asks which to edit, or to clear the maximum. */
function pair({ ctx, edit }: Edits, label: string, dk: KeySpec, dv: number | null, mk: KeySpec, mv: number | null, sub?: string): RowSpec {
  const bad = problemAt(ctx, dk.key) ?? problemAt(ctx, mk.key);
  const ch = ctx.changes.get(dk.key) ?? ctx.changes.get(mk.key);
  return {
    key: `${dk.key}|${mk.key}`,
    label,
    value: `default ${show(dk.unit, dv)}\nmax ${show(mk.unit, mv, true)}`,
    sub: bad ? bad.message : sub,
    changed: !!ch,
    chips: bad ? [{ label: "problem", tone: "bad" }] : undefined,
    onEdit: () =>
      edit({
        kind: "menu",
        title: label,
        options: [
          { value: "d", label: "Edit default", hint: show(dk.unit, dv) },
          { value: "m", label: "Edit maximum", hint: show(mk.unit, mv, true) },
          ...(mv != null ? [{ value: "clear", label: "Clear maximum (no bound)", danger: true }] : []),
        ],
        pick: (v) => (v === "d" ? textEdit(ctx, dk, `${label} default`, dv) : v === "m" ? textEdit(ctx, mk, `${label} maximum`, mv, true) : sendValue(ctx.draft, mk, null)),
      }),
  };
}

const outerValue = (ctx: Ctx): number | null => {
  const m = ctx.draft.view!.result.model["policy.yaml"] as { budget?: { work_item_usd?: number | null } } | undefined;
  return m?.budget?.work_item_usd ?? null;
};

function Limits(e: Edits) {
  const { ctx } = e;
  const caps = ctx.p.limits.caps;
  return (
    <>
      <Group title="Instance" note="Dollars across every item. A per-day spend refuses the next agent task; it can't stop one already running." rows={[
        single(e, KEYS.daily, "per day", (ctx.p.limits.daily_usd.value as number | null) ?? null, { sub: "since local midnight" }),
        single(e, KEYS.outer, "per item", outerValue(ctx), { sub: ctx.p.limits.work_item_usd.binding ? `binding: ${ctx.p.limits.work_item_usd.binding.key} ${show("usd", ctx.p.limits.work_item_usd.binding.value)}` : "a second dollar cap on each item; the lower one wins" }),
      ]} />
      {LEVELS.filter((l) => caps[l]).map((level) => (
        <Group
          key={level}
          title={LEVEL_LABEL[level].name}
          note={LEVEL_LABEL[level].sub}
          foot={level === "tasks" ? "A wait's timeout is its task's wall clock, so this maximum is also the longest any wait may wait." : undefined}
          rows={Object.keys(caps[level]).map((cap) => {
            const c = caps[level][cap];
            const label = CAP_LABEL[cap] ?? { name: cap, unit: "min" as const };
            const row = pair(e, label.name, capKey("default", level, cap), c.default.value, capKey("maximum", level, cap), c.maximum.value, c.maximum.source && c.maximum.source !== level ? `max from ${c.maximum.source.replace("_", " ")}` : undefined);
            return { ...row, key: `${level}.${cap}` };
          })}
        />
      ))}
    </>
  );
}

const SOURCE: Record<string, string> = { loop: "the loop's own", node: "its node", chain: "its chain", defaults: "defaults:", maxima: "maxima:", loops: "loops:", default: "default:" };

function Loops(e: Edits) {
  const { ctx, edit } = e;
  const { p, draft } = ctx;
  const sev = p.findings.loop_severities.value ?? [];
  const stuck = p.escalation.auto_escalate_stuck?.value !== false;
  const stuckCh = ctx.changes.has(STUCK_KEY.key);
  const entry = (key: string) => p.loops.entries.find((x) => x.key === key);
  const stray = p.loops.entries.filter((x) => !x.live);
  const num = (v: unknown) => (typeof v === "number" ? v : null);

  const setLoop = async (key: string, field: "max_attempts" | "wall_clock_s", unit: "count" | "s-as-min", t: string): Promise<string | null> => {
    const v = parse(unit, t);
    if ("error" in v) return v.error;
    if (v.value === null) return "A loop's own value cannot be blank: remove its entry instead.";
    const a = await draft.ops([{ op: "set_loop", key, [field]: v.value }], { quiet: true });
    return a.status === 200 ? null : detailOf(a.body);
  };
  const removeLoop = (key: string) => draft.ops([{ op: "remove_loop", key }], { quiet: true }).then((a) => (a.status === 200 ? null : detailOf(a.body)));
  const loopEdit = (title: string, key: string, field: "max_attempts" | "wall_clock_s", unit: "count" | "s-as-min", value: number): Edit => ({
    kind: "text", title, help: `In ${UNIT_WORD[unit]}.`, value: raw(unit, value), set: (t) => setLoop(key, field, unit, t),
  });

  return (
    <>
      <Group title="Fix loops" note="Defaults for every fix loop that doesn't set its own. A chain, node or loop may set its own." rows={[
        pair(e, "attempts", KEYS.attemptsDefault, p.limits.max_attempts.default.value, KEYS.attemptsMax, p.limits.max_attempts.maximum.value),
        pair(e, "wall clock", KEYS.wallDefault, p.limits.timeout_minutes.default.value, KEYS.wallMax, p.limits.timeout_minutes.maximum.value),
      ]} />
      <Group title="Every fix loop" note="The loops in published chains, and the numbers each one runs on." rows={[
        single(e, KEYS.loopAttempts, "loops without their own: attempts", p.loops.default.attempts),
        single(e, KEYS.loopWall, "loops without their own: wall clock", p.loops.default.wall_clock_s),
        ...p.loops.live.map((l): RowSpec => ({
          key: `loop:${l.key}`,
          label: `${l.node} · ${l.chain}`,
          mono: true,
          value: `${show("count", l.attempts.value)} · ${show("s-as-min", l.wall_clock_s.value)}`,
          sub: `${SOURCE[l.attempts.source ?? ""] ?? l.attempts.source ?? "default"}`,
          onEdit: () => edit({
            kind: "menu", title: `${l.node} · ${l.chain}`,
            options: [
              { value: "a", label: "Edit attempts", hint: show("count", l.attempts.value) },
              { value: "w", label: "Edit wall clock", hint: show("s-as-min", l.wall_clock_s.value) },
              ...(entry(l.key) ? [{ value: "rm", label: "Remove its entry", danger: true }] : []),
            ],
            pick: (v) => (v === "a" ? loopEdit(`${l.node} attempts`, l.key, "max_attempts", "count", l.attempts.value ?? 0) : v === "w" ? loopEdit(`${l.node} wall clock`, l.key, "wall_clock_s", "s-as-min", l.wall_clock_s.value ?? 0) : removeLoop(l.key)),
          }),
        })),
      ]} />
      {stray.length > 0 && (
        <Group title="Not a loop in any chain" rows={stray.map((x): RowSpec => ({
          key: `stray:${x.key}`, label: x.key, mono: true, danger: true,
          sub: ctx.problems.find((q) => q.field === `loops.${x.key}` || q.path === `loops.${x.key}`)?.message ?? "names no fix loop in the library",
          value: `${show("count", x.attempts)} · ${show("s-as-min", x.wall_clock_s)}`,
          onEdit: () => edit({ kind: "menu", title: x.key, options: [{ value: "rm", label: "Remove the entry", danger: true }], pick: () => removeLoop(x.key) }),
        }))} />
      )}
      <Group title="Findings that burn a cycle" note="Severities that start another fix cycle. The rest are shown at the gate without looping." rows={SEVERITIES.map((s): RowSpec => ({
        key: s, label: s, sw: sev.includes(s), changed: ctx.changes.has(SEVERITY_KEY.key),
        onSwitch: () => void sendValue(draft, SEVERITY_KEY, SEVERITIES.filter((x) => (x === s ? !sev.includes(x) : sev.includes(x)))),
      }))} />
      <Group
        title="When an item is stuck"
        foot={stuck ? "An item that reports no progress escalates to a person on its own." : "Off: a stuck item waits until a person escalates it."}
        rows={[
          { label: "auto-escalate", sw: stuck, changed: stuckCh, onSwitch: (on) => void sendValue(draft, STUCK_KEY, on) },
          ...(stuck ? [
            single(e, KEYS.stuckCap, "escalations per item", num(p.escalation.auto_escalate_stuck_cap?.value)),
            single(e, KEYS.stuckDelay, "delay before escalating", num(p.escalation.auto_escalate_delay_s?.value), { sub: "0 escalates at once" }),
            ...(p.escalation.auto_review_attempts ? [single(e, KEYS.reviewAttempts, "review attempts", num(p.escalation.auto_review_attempts.value))] : []),
          ] : []),
        ]}
      />
    </>
  );
}

function Housekeeping(e: Edits) {
  const { ctx } = e;
  const { p } = ctx;
  const max = p.housekeeping.max_concurrent.value;
  const cleanup = p.housekeeping.storage_auto_cleanup?.value ?? null;
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return (
    <>
      <Group title="Runs" note="How this install runs. Instance-wide: no repo, chain or item can change these." rows={[
        single(e, KEYS.concurrent, "max active items", max, { sub: `${ctx.active != null ? `${ctx.active} of ${max ?? "—"} slots in use now. ` : ""}Auto-intake only fills free slots.` }),
        single(e, KEYS.relaunch, "rate-limit relaunches", num(p.retries.rate_limit_retries?.value), { sub: "before the item stops for a person" }),
      ]} />
      <Group title="Board and forge" rows={[
        single(e, KEYS.archive, "archive after", p.housekeeping.archive_after_days.value, { never: "never", sub: "completed and abandoned items; empty never archives" }),
        single(e, KEYS.forge, "forge call timeout", num(p.retries.forge_cli_timeout_s?.value), { sub: "read at startup" }),
      ]} />
      <Group title="Storage" rows={[
        sizeRow(e, KEYS.storageLimit, "limit", p.housekeeping.storage_limit?.value ?? null, "no limit", "over it, new starts wait until you make room"),
        sizeRow(e, KEYS.storageQuota, "quota", p.housekeeping.storage_quota?.value ?? null, p.housekeeping.storage_quota_default ?? "not set", "over it, Kraft warns; blank is 80% of the limit"),
        sizeRow(e, KEYS.storageCleanup, "automatic clean-up", cleanup, "off", cleanup ? `archives finished items ended ${cleanup} ago or more` : "over the limit, starts wait until you make room", parseAge, "Hours or days: 12h, 24h, 2d. Leave empty to turn it off."),
      ]} />
    </>
  );
}
