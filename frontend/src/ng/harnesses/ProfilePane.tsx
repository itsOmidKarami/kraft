import { useState } from "react";
import { Link } from "react-router-dom";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { Head, Kv, Note } from "../templates/panes/controls";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { problemText } from "../templates/problems";
import { Button } from "../ui/Button";
import { AddEntry, EntryEditor } from "./HarnessPane";
import { chainLink } from "./Lanes";
import { type HProblem, type Resolved, problemsOfProfile, taskName } from "./model";
import { entryOp, useRun } from "./ops";
import type { ProviderStatus } from "./useProviders";

type Props = {
  draft: ConfigDraft;
  r: Resolved;
  name: string;
  /** A provider entry's lane, when one is open. */
  lane: string | null;
  providers: ProviderStatus[];
  problems: HProblem[];
  changes: Set<string>;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  onLane: (l: string | null) => void;
  onHarness: (id: string) => void;
  /** After a rename or a removal the selection moves. */
  onGone: (to: string | null) => void;
};

/** A profile (Decisions §11 Profile selected): a list of provider entries, never a matrix.
 *  Its pane, or the pane of one of its entries when a lane is open. */
export function ProfilePane(p: Props) {
  const { draft, r, name, lane } = p;
  const profile = r.profiles[name];
  const { run, error, clear } = useRun(draft);
  const [mode, setMode] = useState<null | "rename" | "copy" | "remove">(null);
  const [id, setId] = useState("");
  const [broken, setBroken] = useState<{ chain: string; path: string }[] | null>(null);
  const own = problemsOfProfile(r, p.problems, name);
  const status = (provider: string) => p.providers.find((s) => s.id === provider);
  const changed = p.changes.has(`profiles.${name}`);

  if (lane && profile) {
    const entry = profile.providers[lane];
    const hs = r.harnesses.filter((h) => h.provider === lane);
    return (
      <Inspector id="harnesses-entry" open={p.open} size={p.size} crumbs={[{ label: "Profiles", onClick: () => p.onLane(null) }, { label: name, onClick: () => p.onLane(null) }]} icon="bot" title={lane} sub={`provider entry of ${name}`} onCollapse={p.onCollapse} onExpand={p.onExpand}
        footer={entry ? <><Button variant="danger" onClick={() => void run(entryOp(name, lane, null))}>Remove entry</Button></> : undefined}>
        <Head>Provider entry</Head>
        {entry ? <EntryEditor draft={draft} profile={name} provider={lane} entry={entry} status={status(lane)} changed={changed} /> : <AddEntry draft={draft} profile={name} provider={lane} status={status(lane)} />}
        <Head>Runs on · {hs.length}</Head>
        {hs.map((h) => <Kv key={h.id} k={h.id} v={`${h.state[0].toUpperCase()}${h.state.slice(1)} →`} onClick={() => p.onHarness(h.id)} />)}
        {error && <p className="hn-error" role="alert">{error}</p>}
      </Inspector>
    );
  }

  const submit = async () => {
    clear();
    if (mode === "rename") {
      const { ok, answer } = await run({ op: "rename_profile", name, to: id.trim() });
      if (ok) {
        const res = (answer.body as { ops?: { op: string; result?: { broken?: { chain: string; path: string }[] } }[] }).ops?.find((o) => o.op === "rename_profile")?.result;
        setBroken(res?.broken?.length ? res.broken : null);
        setMode(null);
        p.onGone(id.trim());
      }
    } else if (mode === "copy") {
      const { ok } = await run({ op: "add_profile", name: id.trim(), copy_from: name });
      if (ok) {
        setMode(null);
        p.onGone(id.trim());
      }
    }
  };
  const remove = async () => {
    const { ok } = await run({ op: "remove_profile", name });
    if (ok) p.onGone(null);
  };

  const footer = mode === "rename" || mode === "copy" ? (
    <form className="hn-form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <input className="hn-input" aria-label={mode === "rename" ? "New name" : "Name of the copy"} autoFocus placeholder={mode === "rename" ? "new name" : "name of the copy"} value={id} onChange={(e) => setId(e.target.value)} />
      <Button type="submit" variant="primary" disabled={!id.trim()}>{mode === "rename" ? "Rename" : "Copy"}</Button>
      <Button onClick={() => { setMode(null); clear(); }}>Cancel</Button>
    </form>
  ) : (
    <>
      <Button onClick={() => { setMode("rename"); setId(name); clear(); }}>Rename</Button>
      <Button onClick={() => { setMode("copy"); setId(""); clear(); }}>Copy</Button>
      <span className="bp-gap" />
      <Button variant="danger" onClick={() => void remove()}>Remove</Button>
    </>
  );

  return (
    <Inspector
      id="harnesses-profile"
      open={p.open}
      size={p.size}
      crumbs={[{ label: "Profiles", onClick: () => p.onLane(null) }]}
      icon="layers"
      title={name}
      sub={`agent profile · ${Object.keys(profile?.providers ?? {}).length} providers`}
      prob={own[0] ? { msg: problemText(own[0]), fix: own[0].fix } : undefined}
      onCollapse={p.onCollapse}
      onExpand={p.onExpand}
      footer={footer}
    >
      <Head>Profile</Head>
      <Note>Model and effort are per provider: click a lane. Read live at every launch.</Note>
      {Object.keys(profile?.providers ?? {}).length === 0 && <Note>No provider entries yet.</Note>}
      <Head>Used by · {profile?.tasks.length ?? 0}</Head>
      {(profile?.tasks ?? []).map((t) => (
        <Link key={`${t.chain}/${t.path}`} className="hn-used" to={chainLink({ ...t, profile: name, fallback: false })}>{taskName(t)}</Link>
      ))}
      {(profile?.tasks.length ?? 0) === 0 && <Note>No task selects this profile.</Note>}
      {(profile?.used_by_fallback.length ?? 0) > 0 && <Note>Named in the fallback of {profile!.used_by_fallback.join(", ")}.</Note>}
      {broken && (
        <div className="hn-broken" role="alert">
          <strong>These tasks still name the old profile and no longer launch:</strong>
          <ul>{broken.map((t) => <li key={`${t.chain}/${t.path}`}>{taskName(t)}</li>)}</ul>
        </div>
      )}
      {error && <p className="hn-error" role="alert">{error}</p>}
    </Inspector>
  );
}
