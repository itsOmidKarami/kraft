import { useState } from "react";
import type { ConfigDraft } from "../draft/useConfigDraft";
import type { Result, Scope } from "../draft/types";
import { authoredAt, authoredNodes, kindOf, normalise, resolvedAt, valueAt, type NodeA, type Step, type Task } from "../draft/view";
import { Head, Kv, Note, PauseText, SelectRow, type Option } from "./controls";
import { FallbackRows } from "./Fallback";
import type { PaneKind } from "./describe";
import { useProviders } from "../../harnesses/useProviders";
import { effortsFor, useHarnessOptions } from "./useHarnessOptions";

export type PaneCtx = {
  r: Result;
  scope: Scope;
  path: string;
  draft: ConfigDraft;
  /** Select a canonical path (and open its pane). */
  goTo: (path: string) => void;
};

const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The library components a chain uses: every `extends` it writes, anywhere. */
function used(nodes: NodeA[]): string[] {
  const out = new Set<string>();
  const visit = (x: unknown) => {
    if (Array.isArray(x)) x.forEach(visit);
    else if (x && typeof x === "object") {
      for (const [k, v] of Object.entries(x)) {
        if (k === "extends" && typeof v === "string") out.add(v);
        else visit(v);
      }
    }
  };
  visit(nodes);
  return [...out];
}

/** Overview, per kind (the prototype's `sidePaneInner`; Decisions §9 Side pane). */
export function Overview({ kind, ctx }: { kind: PaneKind; ctx: PaneCtx }) {
  switch (kind) {
    case "chain": return <ChainOverview ctx={ctx} />;
    case "gate": return <GateOverview ctx={ctx} />;
    case "node": return <NodeOverview ctx={ctx} />;
    case "step": return <StepOverview ctx={ctx} />;
    case "fixloop": return <FixLoopOverview ctx={ctx} />;
    default: return <TaskOverview kind={kind} ctx={ctx} />;
  }
}

function ChainOverview({ ctx }: { ctx: PaneCtx }) {
  const { r, scope, draft } = ctx;
  const nodes = authoredNodes(r, scope);
  const gates = nodes.filter((n) => kindOf(r, n) === "gate").length;
  const repos = r.impact.repos ?? [];
  const running = r.impact.running ?? 0;
  const libs = used(nodes);
  return (
    <>
      <PauseText label="description" long rows={2} value={str(authoredAt(r, scope, "")?.description)} placeholder="What this chain is for" onText={(t) => draft.field("", "description", t, true)} onBlur={draft.flush} />
      <Kv k="size" v={`${plural(nodes.length - gates, "exec node")} · ${plural(gates, "gate")}`} />
      <Kv k="default for" v={repos.length ? repos.join(", ") : "no repos"} mono muted={!repos.length} />
      <Kv k="open" v={`${plural(running, "item")} · keep their version`} muted={!running} />
      <Kv k="library" v={libs.length ? libs.join(", ") : "none"} mono muted={!libs.length} />
    </>
  );
}

function GateOverview({ ctx }: { ctx: PaneCtx }) {
  const { r, scope, path, draft } = ctx;
  const nodes = authoredNodes(r, scope);
  const i = nodes.findIndex((n) => n.id === path);
  const v = (f: string) => valueAt(r, scope, path, f);
  const artifact = str(v("artifact"));
  const required = v("artifact_required") === true;
  const docs = r.resolved?.documents[path] ?? [];
  const docOptions: Option[] = [{ value: "", label: "none" }, ...docs.map((d) => ({ value: d, label: d }))];
  if (artifact && !docs.includes(artifact)) docOptions.push({ value: artifact, label: `${artifact} (not produced)` });
  const earlier = nodes.slice(0, Math.max(0, i));
  const rejectTo = str(authoredAt(r, scope, path)?.reject_to);
  const rejectOptions: Option[] = [{ value: "", label: "not set" }, ...earlier.map((n) => ({ value: n.id, label: kindOf(r, n) === "gate" ? `${n.id} (gate)` : n.id, disabled: kindOf(r, n) === "gate" }))];
  if (rejectTo && !earlier.some((n) => n.id === rejectTo)) rejectOptions.push({ value: rejectTo, label: `${rejectTo} (not earlier)` });
  const reviewer = resolvedAt(r, `${path}.auto_review`) ?? authoredAt(r, scope, `${path}.auto_review`);
  const bad = (f: string) => r.problems.some((p) => p.path === path && p.field === f);
  return (
    <>
      <PauseText label="message" long rows={2} value={str(v("message"))} placeholder="What the reviewer should decide" onText={(t) => draft.field(path, "message", t, true)} onBlur={draft.flush} />
      <SelectRow
        label="document"
        value={artifact}
        options={docOptions}
        bad={bad("artifact")}
        onPick={(x) => draft.field(path, "artifact", x || null)}
        // R31: "required" appears once a document is picked.
        check={artifact ? { label: "required", on: required, onToggle: () => draft.field(path, "artifact_required", required ? null : true) } : undefined}
        sub={!artifact ? "Nothing is needed to approve." : required ? "Approval waits until the document exists." : "It can be approved without the document."}
      />
      <SelectRow
        label="reject to"
        value={rejectTo}
        options={rejectOptions}
        bad={bad("reject_to")}
        onPick={(x) => draft.field(path, "reject_to", x || null)}
        sub={rejectTo ? undefined : "Reject goes back to the previous exec node."}
      />
      <PauseText label="timeout" mono value={str(v("timeout"))} placeholder="none · e.g. 2d" bad={bad("timeout")} onText={(t) => draft.field(path, "timeout", t.trim() || null, true)} onBlur={draft.flush} />
      <Kv k="auto review" v={reviewer ? `${str(reviewer.id) || "reviewer"} · ${str(reviewer.kind) || "agent"}` : "none · + add"} mono={!!reviewer} muted={!reviewer} onClick={() => ctx.goTo(reviewer ? `${path}.auto_review` : path)} />
    </>
  );
}

/** A node's steps as the canvas draws them: resolved, else what the page last saw. */
const stepsOf = (ctx: PaneCtx, node: string): Step[] => ctx.draft.resolvedNode(node)?.steps ?? [];

function NodeOverview({ ctx }: { ctx: PaneCtx }) {
  const { r, path } = ctx;
  const steps = stepsOf(ctx, path);
  const node = ctx.draft.resolvedNode(path);
  const tasks = steps.flatMap((s) => s.tasks);
  const produces = [...new Set(tasks.map((t) => t.produces).filter((x): x is string => typeof x === "string"))];
  const fix = node?.fix_loop as (NodeA & { judge?: Task; max_attempts?: number }) | null | undefined;
  const fixSteps = normalise(fix)?.steps ?? [];
  const esc = node?.escalation as Task | null | undefined;
  const onFailure = node?.on_failure as NodeA | null | undefined;
  const delay = Math.round((r.policy_values?.auto_escalate_delay_s ?? 0) / 60);
  const lone = steps.length === 1 && steps[0].id === "main";
  return (
    <>
      <Head>{steps.length ? "Steps, in order" : "Steps"}</Head>
      {!steps.length && <Note>No steps yet. Open the node to add the first one, or extend a library node.</Note>}
      {steps.map((s) => (
        <Kv
          key={s.id}
          k={lone ? s.tasks.map((t) => t.id).join(", ") || "main" : s.id}
          v={s.tasks.length > 1 ? `${s.tasks.length} tasks · parallel` : plural(s.tasks.length, "task")}
          onClick={() => ctx.goTo(`${path}.${s.id}`)}
        />
      ))}
      <Head>Handling</Head>
      <Kv k="produces" v={produces.length ? produces.join(", ") : "nothing"} mono muted={!produces.length} />
      <Kv k="fix loop" v={fix ? `${plural(fixSteps.length, "repair step")}${fix.judge ? " · judge" : ""}` : "none"} muted={!fix} onClick={() => ctx.goTo(fix ? `${path}.fix_loop` : path)} />
      <Kv k="escalation" v={esc ? str(esc.id) : `none · auto after ${delay}m`} mono={!!esc} muted={!esc} onClick={esc ? () => ctx.goTo(`${path}.escalation.${str(esc.id)}`) : undefined} />
      <Kv k="on failure" v={onFailure ? "node handler" : "none"} muted={!onFailure} />
    </>
  );
}

function StepOverview({ ctx }: { ctx: PaneCtx }) {
  const { r, scope, path } = ctx;
  const step = (resolvedAt(r, path) ?? authoredAt(r, scope, path)) as Step | null;
  const tasks = (step?.tasks ?? []) as Task[];
  return (
    <>
      <Head>Tasks</Head>
      {!tasks.length && <Note>No tasks yet. Use the + slot on the canvas.</Note>}
      {tasks.map((t) => (
        <Kv key={t.id} k={t.id} v={`${str(t.kind) || "?"}${t.extends ? ` · ${str(t.extends)}` : ""}`} onClick={() => ctx.goTo(`${path}.${t.id}`)} />
      ))}
      {step?.on_failure ? <Kv k="on failure" v="step handler" /> : null}
    </>
  );
}

function FixLoopOverview({ ctx }: { ctx: PaneCtx }) {
  const { r, scope, path } = ctx;
  const loop = (resolvedAt(r, path) ?? authoredAt(r, scope, path)) as (NodeA & { judge?: Task }) | null;
  const repair = normalise(loop)?.steps ?? [];
  return (
    <>
      <Head>Run order</Head>
      <Kv k="judge" v={loop?.judge ? `${str(loop.judge.id)} · decides continue, accept or stop` : "none · repairs until attempts run out"} muted={!loop?.judge} onClick={loop?.judge ? () => ctx.goTo(`${path}.judge`) : undefined} />
      <Kv k="repair" v={repair.map((s) => (s.id === "main" && repair.length === 1 ? s.tasks.map((t) => t.id).join(", ") || "empty" : s.id)).join(" → ") || "empty"} mono />
      <Note>Attempt 1 skips the judge. The judge can't exceed the limits on the Config tab.</Note>
    </>
  );
}

function TaskOverview({ kind, ctx }: { kind: PaneKind; ctx: PaneCtx }) {
  const { r, scope: sc, path, draft } = ctx;
  const v = (f: string) => valueAt(r, sc, path, f);
  const opts = useHarnessOptions();
  const taskKind = str(v("kind"));
  const set = (f: string, pause = false) => (x: unknown) => draft.field(path, f, x === "" ? null : x, pause);
  const prompt = str(v("prompt"));
  const harness = str(v("harness")) || "claude";
  const profile = str(v("profile"));
  // Picking "model + effort" shows its fields before a model is typed.
  const [routeChoice, setRouteChoice] = useState<string | null>(null);
  const route = routeChoice ?? (profile || !(v("model") || v("effort")) ? "profile" : "model");
  const scope = str(v("scope")) || "once";
  const loading = typeof opts === "string";
  const harnesses: Option[] = loading ? [{ value: harness, label: harness }] : opts.harnesses.profiles.map((p) => ({ value: p.id, label: p.id }));
  const profiles: Option[] = [{ value: "", label: "harness default" }, ...(loading ? (profile ? [{ value: profile, label: profile }] : []) : opts.harnesses.agent_profiles.map((p) => ({ value: p.id, label: p.id })))];
  const efforts = effortsFor(opts, harness);
  const target = str(v("target"));
  // The closed sets come with the draft (`result.choices`), never typed in here.
  const choices = r.choices;
  const listed = useProviders();
  const provider = loading ? harness : opts.harnesses.profiles.find((p) => p.id === harness)?.provider ?? harness;
  const models = listed.find((p) => p.id === provider)?.models ?? [];
  return (
    <>
      {kind === "judge" && <Note>Runs before the repair from attempt 2, and decides continue, accept or stop.</Note>}
      {kind === "esc" && <Note>Runs once per entry when the node is stuck. Success reruns the node; failure or a question parks it for you.</Note>}
      {!taskKind && <Note bad>This task has no kind. Set one in YAML or remove it.</Note>}
      {taskKind === "agent" && (
        <>
          <PauseText label="prompt" long rows={4} value={prompt} placeholder="What should the agent do?" required autoFocus={!prompt} bad={!prompt} sub={!prompt ? "Required." : undefined} onText={(t) => set("prompt", true)(t)} onBlur={draft.flush} />
          {opts === "failed" && <Note bad>Couldn't load harnesses.</Note>}
          <SelectRow label="harness" value={harness} options={harnesses} onPick={set("harness")} />
          <SelectRow
            label="route"
            value={route}
            options={[{ value: "profile", label: "profile" }, { value: "model", label: "model + effort" }]}
            // Profile or model + effort, never both (Decisions §9 Task kinds): setting one clears the other server-side.
            onPick={(x) => {
              setRouteChoice(x);
              draft.field(path, "profile", x === "profile" ? profiles[1]?.value ?? null : null);
            }}
          />
          {route === "profile" ? (
            <SelectRow label="profile" value={profile} options={profiles} onPick={set("profile")} />
          ) : (
            <>
              <PauseText label="model" mono value={str(v("model"))} choices={models.map((m) => ({ value: m }))} listLabel="Models" onText={(t) => set("model", true)(t.trim())} onBlur={draft.flush} />
              {efforts.length ? (
                <SelectRow label="effort" value={str(v("effort"))} options={[{ value: "", label: "default" }, ...efforts.map((e) => ({ value: e, label: e }))]} onPick={set("effort")} />
              ) : (
                <PauseText label="effort" mono value={str(v("effort"))} onText={(t) => set("effort", true)(t.trim())} onBlur={draft.flush} />
              )}
            </>
          )}
          {kind === "review" ? (
            <Note>A gate reviewer has no fallback list and no on-failure handler.</Note>
          ) : (
            <FallbackRows
              value={v("fallback")}
              harness={harness}
              harnesses={loading ? null : opts.harnesses.profiles.map((p) => p.id)}
              profiles={loading ? null : opts.harnesses.agent_profiles.map((p) => p.id)}
              onChange={(next) => draft.field(path, "fallback", next)}
            />
          )}
        </>
      )}
      {taskKind === "builtin" && (
        <>
          <PauseText label="action" mono value={str(v("ref"))} required bad={!v("ref")} sub={!v("ref") ? "Required." : undefined} choices={choices?.ref} closed={!!choices} listLabel="Actions" onText={(t) => set("ref", true)(t.trim())} onBlur={draft.flush} />
          <SelectRow label="runs" value={scope} options={[{ value: "once", label: "once" }, { value: "each_repository", label: "each repository" }]} onPick={set("scope")} />
          {scope === "each_repository" && <SelectRow label="order" value={str(v("execution")) || "sequential"} options={[{ value: "sequential", label: "sequential" }, { value: "parallel", label: "parallel" }]} onPick={set("execution")} />}
        </>
      )}
      {taskKind === "subprocess" && (
        <PauseText label="command" mono value={str(v("command"))} placeholder="e.g. make lint" required bad={!v("command")} sub={!v("command") ? "Required." : undefined} onText={(t) => set("command", true)(t)} onBlur={draft.flush} />
      )}
      {taskKind === "forge" && (
        <>
          <PauseText label="target" mono value={target} required bad={!target} sub={!target ? "Required." : undefined} choices={choices?.target} closed={!!choices} listLabel="Targets" onText={(t) => set("target", true)(t.trim())} onBlur={draft.flush} />
          <SelectRow label="runs" value={scope} options={[{ value: "once", label: "once" }, { value: "each_repository", label: "each repository" }]} onPick={set("scope")} />
          {choices?.target.some((t) => t.value === target && t.waits) && (
            <>
              <PauseText label="check every" mono value={str(v("wait.polling.initial_interval"))} placeholder="30s" onText={(t) => set("wait.polling.initial_interval", true)(t.trim())} onBlur={draft.flush} />
              <PauseText label="gives up after" mono value={str(v("policy.total_time_cap_minutes"))} placeholder="90 (minutes)" sub="Minutes. The wait stops for you after this." onText={(t) => set("policy.total_time_cap_minutes", true)(t.trim() ? Number(t) || t.trim() : null)} onBlur={draft.flush} />
            </>
          )}
        </>
      )}
      {kind === "task" && authoredAt(r, sc, path)?.on_failure !== undefined && <Kv k="on failure" v="task handler" />}
    </>
  );
}
