import { Navigate, useParams, useSearchParams } from "react-router-dom";
import { detailOf } from "../../http";
import { ACCESS, ACCESS_WORD, problemsOfHarness, problemsOfProfile, resolvedOf, type Access, type HProblem, type Resolved } from "../../harnesses/model";
import { entryOp } from "../../harnesses/ops";
import { useProviders } from "../../harnesses/useProviders";
import type { Op } from "../../templates/draft/types";
import { useConfigDraft, type ConfigDraft } from "../../templates/draft/useConfigDraft";
import { problemText } from "../../templates/problems";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";

const send = async (draft: ConfigDraft, op: Op) => {
  const a = await draft.ops([op], { quiet: true });
  return a.status >= 200 && a.status < 300 ? null : detailOf(a.body);
};
const problemRows = (ps: HProblem[]): RowSpec[] => ps.map((p, i) => ({ key: `p${i}`, label: problemText(p), sub: [p.chain, p.path].filter(Boolean).join(" · ") || undefined, chips: [{ label: "problem", tone: "bad" as const }] }));
const changedAt = (draft: ConfigDraft, needle: string) => !!draft.view?.result.changes.some((c) => c.path.includes(needle));

/** `/templates/harnesses`. The desktop keeps its selection in the query (`?harness=` / `?profile=`); that address opens the phone's own page for it, with the rest of the query, so a resize keeps the place. */
export function HarnessesList() {
  const [params] = useSearchParams();
  const harness = params.get("harness");
  const profile = params.get("profile");
  const rest = new URLSearchParams(params);
  rest.delete("harness");
  rest.delete("profile");
  const query = rest.size ? `?${rest}` : "";
  if (harness) return <Navigate to={`/templates/harnesses/${encodeURIComponent(harness)}${query}`} replace />;
  if (profile) return <Navigate to={`/templates/harnesses/profiles/${encodeURIComponent(profile)}${query}`} replace />;
  return <HarnessesIndex />;
}

/** Harnesses, Profiles, the agent tasks and the defaults. A harness page edits its Access only; a profile page edits its entries' model and effort; the defaults edit as the desktop's Config tab does. */
function HarnessesIndex() {
  const draft = useConfigDraft("harnesses", "harnesses");
  const { edit, node } = useEditor();
  const r = draft.view ? resolvedOf(draft.view.result) : null;
  const problems = (draft.view?.result.problems ?? []) as HProblem[];
  const tasks = r ? r.harnesses.flatMap((h) => h.tasks.filter((t) => !t.fallback).map((t) => ({ ...t, harness: h.id }))) : [];
  return (
    <AreaScreen title="Harnesses" sub="The agent runtimes Kraft launches, the profiles that pick a model, and which harness each task uses." draft={draft}>
      {draft.status === "error" && <p className="ph-error" role="alert">The harnesses could not be read.</p>}
      {problems.length > 0 && <Group title="Problems" rows={problemRows(problems)} />}
      {r && (
        <>
          <Group title="Harnesses" rows={r.harnesses.map((h): RowSpec => ({ key: h.id, label: h.id, mono: true, sub: `${h.provider ?? "?"}${h.executable_found ? "" : " · not found on this machine"}`, to: `/templates/harnesses/${encodeURIComponent(h.id)}`, chips: [{ label: ACCESS_WORD[h.state], tone: h.state === "never" ? "warn" : undefined }, ...(problemsOfHarness(r, problems, h.id).length ? [{ label: "problem", tone: "bad" as const }] : [])] }))} />
          <Group title="Profiles" rows={Object.entries(r.profiles).map(([name, p]): RowSpec => ({ key: name, label: name, mono: true, sub: Object.entries(p.providers).map(([prov, e]) => `${prov} ${e.model ?? "—"}`).join(" · "), to: `/templates/harnesses/profiles/${encodeURIComponent(name)}`, chips: problemsOfProfile(r, problems, name).length ? [{ label: "problem", tone: "bad" as const }] : undefined }))} />
          <Group title="Tasks" note="Each agent task and the harness it runs on. Change it in the chain." rows={tasks.map((t, i): RowSpec => ({ key: `${t.chain}:${t.path}:${i}`, label: t.path, mono: true, sub: t.chain, value: `${t.harness}${t.profile ? ` · ${t.profile}` : ""}` }))} />
          <Group
            title="Defaults"
            rows={[
              { label: "escalation runs on", value: r.escalation.harness ?? `item's harness (${r.escalation_effective.harness})`, changed: changedAt(draft, "escalation"), onEdit: () => edit({ kind: "choice", title: "Escalation runs on", help: "\"item's harness\" follows the one the item's latest agent task used. Harnesses set to Never aren't offered.", value: r.escalation.harness ?? "", options: [{ value: "", label: "item's harness" }, ...r.harnesses.filter((h) => h.state !== "never" || h.id === r.escalation_effective.harness).map((h) => ({ value: h.id, label: h.state === "never" ? `${h.id} (Never)` : h.id }))], set: (v) => send(draft, { op: "set_escalation", harness: v || null, grants: r.escalation.grants }) }) },
              { label: "allowed tools", value: r.allowed_tools == null ? "every tool" : r.allowed_tools.join(", ") || "none", changed: changedAt(draft, "allowed_tools"), onEdit: () => edit({ kind: "text", title: "Allowed tools", help: "Comma separated. Empty allows no tools; remove the list in YAML to allow every tool.", value: (r.allowed_tools ?? []).join(", "), set: (v) => send(draft, { op: "set_allowed_tools", tools: list(v) }) }) },
              { label: "escalation grants", value: (r.escalation.grants ?? []).join(", ") || "none", changed: changedAt(draft, "grants"), onEdit: () => edit({ kind: "text", title: "Escalation grants", help: "Comma separated. What every escalation turn may do on top of its node's own grants.", value: (r.escalation.grants ?? []).join(", "), listed: { choices: draft.view?.result.choices?.grants ?? [], closed: true, multiple: true, noun: "grant" }, set: (v) => send(draft, { op: "set_escalation", harness: r.escalation.harness, grants: list(v) }) }) },
            ]}
          />
        </>
      )}
      {node}
    </AreaScreen>
  );
}

const list = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

/** `/templates/harnesses/:id`: its Access (editable), and what it is. */
export function HarnessView() {
  const { id = "" } = useParams();
  const draft = useConfigDraft("harnesses", "harnesses");
  const { edit, node } = useEditor();
  const r = draft.view ? resolvedOf(draft.view.result) : null;
  const h = r?.harnesses.find((x) => x.id === id);
  const problems = r ? problemsOfHarness(r, (draft.view!.result.problems ?? []) as HProblem[], id) : [];
  return (
    <AreaScreen title={id} sub={h?.executable ?? h?.provider ?? undefined} draft={draft}>
      {r && !h && <p className="ph-empty">There is no harness {id}.</p>}
      {problems.length > 0 && <Group title="Problems" rows={problemRows(problems)} />}
      {h && (
        <>
          <Group
            rows={[
              { label: "access", value: ACCESS_WORD[h.state], changed: changedAt(draft, id), onEdit: () => edit({ kind: "choice", title: "Access", help: "Available: tasks and the maxima list it. Override: only the maxima list it. Never: neither.", value: h.state, options: ACCESS.map((a) => ({ value: a.value, label: a.label })), set: (v) => send(draft, { op: "set_access", harness: id, state: v as Access }) }) },
              { label: "provider", value: h.provider ?? "not set", mono: true },
              { label: "enabled", value: h.enabled ? "yes" : "no" },
              { label: "executable", value: h.executable ?? "the provider's own command", sub: h.executable_found ? "found on this machine" : "not found on this machine", mono: true },
              ...Object.entries(h.defaults).map(([k, v]): RowSpec => ({ key: k, label: k, value: v, mono: true })),
            ]}
          />
          <p className="ph-note">A harness's own fields are edited in YAML on a computer; the toggle above shows the file.</p>
          <Group title={`Tasks · ${h.tasks.length}`} rows={h.tasks.map((t, i): RowSpec => ({ key: `${t.chain}:${t.path}:${i}`, label: t.path, mono: true, sub: [t.chain, t.profile, t.fallback ? "fallback" : null].filter(Boolean).join(" · ") }))} />
        </>
      )}
      {node}
    </AreaScreen>
  );
}

/** `/templates/harnesses/profiles/:name`: model and effort per provider. */
export function ProfileView() {
  const { name = "" } = useParams();
  const draft = useConfigDraft("harnesses", "harnesses");
  const providers = useProviders();
  const { edit, node } = useEditor();
  const r: Resolved | null = draft.view ? resolvedOf(draft.view.result) : null;
  const p = r?.profiles[name];
  const problems = r ? problemsOfProfile(r, (draft.view!.result.problems ?? []) as HProblem[], name) : [];
  return (
    <AreaScreen title={name} sub="Profile · model and effort per provider" draft={draft}>
      {r && !p && <p className="ph-empty">There is no profile {name}.</p>}
      {problems.length > 0 && <Group title="Problems" rows={problemRows(problems)} />}
      {p && Object.entries(p.providers).map(([prov, e]) => {
        const status = providers.find((x) => x.id === prov);
        return (
          <Group
            key={prov}
            title={prov}
            rows={[
              { label: "model", value: e.model || "not set", mono: true, changed: changedAt(draft, `profiles.${name}.providers.${prov}`), onEdit: () => edit({ kind: "text", title: `${prov} model`, help: status?.models.length ? `Known: ${status.models.slice(0, 6).join(", ")}` : undefined, value: e.model ?? "", set: (v) => send(draft, entryOp(name, prov, { ...e, model: v.trim() })) }) },
              ...((status?.efforts.length ?? 0) > 0 ? [{ label: "effort", value: e.effort || "default", changed: false, onEdit: () => edit({ kind: "choice", title: `${prov} effort`, help: `What ${prov} accepts.`, value: e.effort ?? "", options: [{ value: "", label: "default" }, ...status!.efforts.map((x) => ({ value: x, label: x }))], set: (v) => send(draft, entryOp(name, prov, { model: e.model, effort: v })) }) } as RowSpec] : []),
            ]}
          />
        );
      })}
      {node}
    </AreaScreen>
  );
}
