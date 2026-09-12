import { useEffect, useState } from "react";
import { Check, Plus } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { OverflowMenu, SectionLabel, Tabs } from "../../components/ui";
import type { SteeringList } from "../../types";
import { PageHead, PhoneHeader, usePhone, useResource } from "./shared";

/* ── 5c-bis steering (design 30, phone m14 right) ─────────────────────────── */

export function SteeringPage() {
  const { value, error, reload } = useResource(() => api.getSteering());
  const { value: reposValue } = useResource(() => api.getRepos());
  const { value: registryValue } = useResource(() => api.getRegistry());
  const [params, setParams] = useSearchParams();
  const selected = params.get("file");
  const [draft, setDraft] = useState("");
  const [loaded, setLoaded] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"edit" | "diff">("edit");
  const phone = usePhone();
  const list: SteeringList = value ?? { files: [], max_bytes: 0 };
  const repos = reposValue?.repos ?? [];
  const hooks = registryValue?.hooks ?? {};

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

  const whoUses = (name: string) => {
    const repoNames = repos.filter((r) => r.steering.includes(name)).map((r) => r.name);
    const hookNames = Object.entries(hooks)
      .filter(([, b]) => b.steering?.includes(name))
      .map(([h]) => h);
    const who = [...repoNames, ...hookNames];
    return who.length ? who.join(", ") : "unused";
  };

  const create = () => {
    const name = window.prompt("New steering file (a bare name, no extension)");
    if (!name) return;
    setParams({ file: name });
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
      setParams({});
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
  // "assembled for repo-a" (design 30's own hedge — one repo, not a global
  // number): this file's bytes plus that repo's *other* steering files, for
  // the first repo that references it.
  const assembledRepo = selected ? repos.find((r) => r.steering.includes(selected)) : null;
  const assembled = assembledRepo
    ? draftBytes +
      list.files
        .filter((f) => f.name !== selected && assembledRepo.steering.includes(f.name))
        .reduce((sum, f) => sum + (f.bytes ?? 0), 0)
    : draftBytes;

  const editor = selected !== null && (
    <>
      {!phone && (
        <div className="settings-head">
          <h2>{selected}.md</h2>
          <span className="settings-note">
            {list.files.find((f) => f.name === selected)?.bytes ?? draftBytes} B ·{" "}
            {whoUses(selected)}
          </span>
          <OverflowMenu
            items={[
              {
                label: "Delete",
                danger: true,
                confirm: `Delete ${selected}.md? Anything that references it stops resolving.`,
                onSelect: remove,
              },
            ]}
          />
        </div>
      )}
      <Tabs
        tabs={[
          { id: "edit", label: "edit" },
          { id: "diff", label: "diff vs saved" },
        ]}
        value={tab}
        onChange={(id) => setTab(id as "edit" | "diff")}
      />
      {tab === "edit" ? (
        <textarea
          id="steering-body"
          aria-label="steering body"
          className="input mono template-yaml"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : (
        <DraftDiff before={loaded} after={draft} />
      )}
      <div className="save-row">
        {/* Phone's Save lives in the PhoneHeader action slot instead — one
            Save button, not two. */}
        {!phone && (
          <button className="btn btn-primary" disabled={busy || draft === loaded} onClick={save}>
            <Check size={14} />
            Save
          </button>
        )}
        <button
          className="btn btn-ghost"
          disabled={busy || draft === loaded}
          onClick={() => setDraft(loaded)}
        >
          Discard
        </button>
        {phone && (
          <OverflowMenu
            items={[
              {
                label: "Delete",
                danger: true,
                confirm: `Delete ${selected}.md? Anything that references it stops resolving.`,
                onSelect: remove,
              },
            ]}
          />
        )}
        <span className="save-hint">
          {message ??
            `${draftBytes} B · assembled for ${assembledRepo?.name ?? "…"}: ${assembled}/${list.max_bytes} B`}
        </span>
      </div>
    </>
  );

  const fileList = (
    <>
      <SectionLabel>Files</SectionLabel>
      {list.files.map((f) => (
        <button
          key={f.name}
          className="facet-opt steering-row"
          aria-pressed={f.name === selected}
          onClick={() => setParams({ file: f.name })}
        >
          <span className="steering-row-name">{f.name}</span>
          <span className="facet-count">
            {whoUses(f.name)} · {f.bytes === null ? "unreadable" : `${f.bytes} B`}
          </span>
        </button>
      ))}
      {list.files.length === 0 && <p className="empty">no steering files yet</p>}
    </>
  );

  if (phone) {
    if (selected === null) {
      return (
        <>
          <PhoneHeader
            back="Settings"
            backTo="/settings"
            title="Steering"
            subtitle={`${list.files.length} files`}
            action={
              <button className="btn btn-primary" onClick={create}>
                +
              </button>
            }
          />
          {error && <p className="form-error">{error}</p>}
          {fileList}
        </>
      );
    }
    return (
      <>
        <PhoneHeader
          back="Steering"
          backTo="/settings/steering"
          title={`${selected}.md`}
          action={
            <button className="btn btn-primary" disabled={busy || draft === loaded} onClick={save}>
              Save
            </button>
          }
        />
        {editor}
      </>
    );
  }

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
        <div className="template-list">{fileList}</div>
        <div className="template-draft">
          {selected === null ? <p className="empty">pick a file, or make one</p> : editor}
        </div>
      </div>
    </>
  );
}
