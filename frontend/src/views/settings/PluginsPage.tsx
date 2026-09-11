import { useState } from "react";
import * as api from "../../api";
import { Switch } from "../../components/ui";
import type { HookBinding } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5c plugins ───────────────────────────────────────────────────────────── */

const adapterOf = (b: HookBinding) =>
  b.kind === "builtin"
    ? `builtin · ${b.handler}`
    : Array.isArray(b.command)
      ? b.command.join(" ")
      : (b.command ?? b.kind);

export function PluginsPage() {
  const { value, error, reload } = useResource(() => api.getRegistry());
  const [draft, setDraft] = useState<Record<string, HookBinding> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hooks = draft ?? value?.hooks ?? {};
  const dirty = draft !== null;

  const toggle = (hook: string) =>
    setDraft({
      ...hooks,
      [hook]: { ...hooks[hook], interactive: !hooks[hook].interactive },
    });

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      const { invalid_templates } = await api.putRegistry(draft);
      setDraft(null);
      await reload();
      const broken = Object.keys(invalid_templates);
      setMessage(broken.length ? `saved · now unresolvable: ${broken.join(", ")}` : "saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title="Plugins"
        note="one binding per hook in v1 · a hook disabled for a repo makes any template using it unresolvable there"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="hook-row hook-head">
        <span>Hook → plugin</span>
        <span>Adapter</span>
        <span>Steer</span>
      </div>
      {Object.entries(hooks).map(([hook, binding]) => (
        <div key={hook} className="hook-row" data-hook={hook}>
          <span className="hook-name">{hook}</span>
          <span className="row-sub">{adapterOf(binding)}</span>
          <Switch
            checked={!!binding.interactive}
            onChange={() => toggle(hook)}
            label={`steerable: ${hook}`}
          />
        </div>
      ))}
      <SaveRow
        onSave={save}
        onDiscard={() => setDraft(null)}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes registry.yaml · validator re-runs · affects intake only, live items keep their chain"
      />
      <p className="settings-foot">
        Steerable means the hook runs a headless agent Kraft can stop and relaunch with a note.
        It comes from the registry (<code>interactive: true</code>), not from the client.
      </p>
    </>
  );
}
