import { useEffect, useState } from "react";
import { Check, Plus } from "@phosphor-icons/react";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { SectionLabel } from "../../components/ui";
import type { SteeringList } from "../../types";
import { PageHead, useResource } from "./shared";

/* ── 5c-bis steering ──────────────────────────────────────────────────────── */

export function SteeringPage() {
  const { value, error, reload } = useResource(() => api.getSteering());
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [loaded, setLoaded] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const list: SteeringList = value ?? { files: [], max_bytes: 0 };

  // The body is fetched per file rather than shipped with the list: the list is
  // a picker, and every body at once is the injection budget over the wire on
  // every page load.
  useEffect(() => {
    if (selected === null) return;
    api
      .getSteeringFile(selected)
      .then((f) => {
        setDraft(f.body);
        setLoaded(f.body);
      })
      .catch(() => {
        setDraft("");
        setLoaded("");
      });
  }, [selected]);

  const create = () => {
    const name = window.prompt("New steering file (a bare name, no extension)");
    if (!name) return;
    setSelected(name);
    setDraft("");
    setLoaded("");
    setMessage(null);
  };

  const save = async () => {
    if (selected === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putSteeringFile(selected, draft);
      setLoaded(draft);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (selected === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.deleteSteeringFile(selected);
      setSelected(null);
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // This file's own size, not a total across the directory: MAX_BYTES is the
  // assembled budget of one repo or hook's steering list, so summing unrelated
  // files reads as over budget when nothing is, and under it when something is.
  // Advisory only — the server checks the real assembled total on save.
  const draftBytes = new TextEncoder().encode(draft).length;

  return (
    <>
      <PageHead
        title="Steering"
        note="Kraft-owned standards injected through the system prompt — never CLAUDE.md, never a file inside the target repo"
        action={
          <button className="btn btn-secondary" onClick={create}>
            <Plus size={14} />
            New
          </button>
        }
      />
      {error && <p className="form-error">{error}</p>}
      <div className="template-editor">
        <div className="template-list">
          <SectionLabel>Files</SectionLabel>
          {list.files.map((f) => (
            <button
              key={f.name}
              className="facet-opt"
              aria-pressed={f.name === selected}
              onClick={() => setSelected(f.name)}
            >
              {f.name}
              <span className="facet-count">
                {f.bytes === null ? "unreadable" : `${f.bytes} B`}
              </span>
            </button>
          ))}
          {list.files.length === 0 && <p className="empty">no steering files yet</p>}
        </div>
        <div className="template-draft">
          {selected === null ? (
            <p className="empty">pick a file, or make one</p>
          ) : (
            <>
              <label className="field-hint" htmlFor="steering-body">
                {selected}.md · a hook or repo names this file, and the assembled block is
                re-checked against the budget on save
              </label>
              <textarea
                id="steering-body"
                aria-label="steering body"
                className="input mono template-yaml desktop-only"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <pre className="template-readout phone-only">{draft}</pre>
              <p className="phone-only open-on-desktop">Open on desktop to edit.</p>
              {showDiff && <DraftDiff before={loaded} after={draft} />}
              <div className="save-row desktop-only">
                <button
                  className="btn btn-primary"
                  disabled={busy || draft === loaded}
                  onClick={save}
                >
                  <Check size={14} />
                  Save
                </button>
                <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
                  Changes
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={busy || draft === loaded}
                  onClick={() => setDraft(loaded)}
                >
                  Discard
                </button>
                <button className="btn btn-ghost" disabled={busy} onClick={remove}>
                  Delete
                </button>
                <span className="save-hint">
                  {message ??
                    `${draftBytes} B · counts toward the ${list.max_bytes} B assembled ` +
                      `budget of any repo or hook that references this file`}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
