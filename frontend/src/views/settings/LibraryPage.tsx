import { useState } from "react";
import { Check } from "@phosphor-icons/react";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { DraftDiff } from "../../components/DraftDiff";
import { SectionLabel, Tabs } from "../../components/ui";
import type { LibraryComponent } from "../../types";
import "./templates.css";
import { PageHead, PhoneHeader, usePhone, useResource } from "./shared";

/* ── Library (Kraft-wdbqo): `templates/library.yaml`, the reusable tasks, steps,
 * nodes and steering profiles chains `extends`.
 *
 * The list is `GET /templates/library`: every component with the chains that
 * use it and the lint issues that name it. The editor is the Chains screen's:
 * the file's text as its author wrote it, a diff against the saved file, and a
 * Save the server refuses, naming why, when the edit would stop a chain that
 * resolves now from resolving (`PUT /templates/library`). */

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function ComponentDetail({ component }: { component: LibraryComponent }) {
  return (
    <>
      <div className="chain-head">
        <h2 className="chain-head-name">{component.id}</h2>
        <span className="chain-head-counts">{component.kind}</span>
      </div>
      <p className="chain-legend">
        {component.used_by.length === 0 ? (
          "used by no chain"
        ) : (
          <>
            used by{" "}
            {component.used_by.map((chain, i) => (
              <span key={chain}>
                {i > 0 && ", "}
                <Link to={`/settings/chains?tpl=${encodeURIComponent(chain)}`}>{chain}</Link>
              </span>
            ))}
          </>
        )}
      </p>
      {typeof component.definition.harness === "string" && (
        <p className="chain-legend">
          runs on harness{" "}
          <Link to={`/settings/harnesses?h=${encodeURIComponent(component.definition.harness)}`}>
            {component.definition.harness}
          </Link>
        </p>
      )}
      {Array.isArray(component.definition.fallback) && (
        <p className="chain-legend" data-testid="task-fallback">
          {component.definition.fallback.length === 0
            ? "no fallback (overrides a profile's list)"
            : `falls back to ${(component.definition.fallback as Record<string, string>[])
                .map((e) => [e.harness, e.model, e.effort].filter(Boolean).join(" / "))
                .join(", then ")}`}
        </p>
      )}
      {component.kind === "steering" && typeof component.definition.instructions === "string" ? (
        <>
          <p className="chain-legend">
            A steering profile is selected by tasks (<code>steering:</code> on a task) and by repositories
            (<code>steering:</code> on <Link to="/settings/repos">Repos</Link>), and frozen into an item at intake.
            Create, edit or remove one in library.yaml, beside.
          </p>
          <pre className="template-readout">{component.definition.instructions}</pre>
        </>
      ) : (
        <pre className="template-readout">{JSON.stringify(component.definition, null, 2)}</pre>
      )}
      {component.issues.length > 0 && (
        <div className="validation" data-valid={false}>
          {component.issues.map((i) => (
            <span key={`${i.chain}:${i.message}`}>
              {i.chain ?? i.file}: {i.message}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

export function LibraryPage() {
  const { value, error, reload } = useResource(() => api.getLibrary());
  const phone = usePhone();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState<string | null>(null);
  const [tab, setTab] = useState<"yaml" | "diff">("yaml");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const components = value?.components ?? [];
  const selected = components.find((c) => c.id === params.get("c")) ?? components[0] ?? null;
  const kinds = [...new Set(components.map((c) => c.kind))];
  const saved = value?.text ?? "";
  const text = draft ?? saved;
  const dirty = draft !== null && draft !== saved;

  const save = async () => {
    if (draft === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putLibrary(draft);
      await reload();
      setDraft(null);
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const list = (
    <div className="template-list">
      {kinds.map((kind) => (
        <div key={kind}>
          <SectionLabel>{kind}</SectionLabel>
          {components
            .filter((c) => c.kind === kind)
            .map((c) => (
              <button
                key={c.id}
                className="facet-opt steering-row"
                aria-pressed={c.id === selected?.id}
                onClick={() => setParams({ c: c.id })}
              >
                <span className="steering-row-name">{c.name}</span>
                <span className="facet-count">
                  {c.used_by.length ? plural(c.used_by.length, "chain") : "unused"}
                  {c.issues.length > 0 && ` · ${plural(c.issues.length, "issue")}`}
                </span>
              </button>
            ))}
        </div>
      ))}
      {value && components.length === 0 && <p className="empty">no library components yet</p>}
    </div>
  );

  return (
    <>
      {phone ? (
        <PhoneHeader back="Settings" backTo="/settings" title="Library" subtitle="~/.kraft/templates/library.yaml" />
      ) : (
        <PageHead title="Library" note="Reusable tasks, steps, nodes and steering that chains extend" />
      )}
      {error && <p className="form-error">{error}</p>}
      <div className="chain-page">
        <div className="template-editor">
          {list}
          <div className="template-draft">{selected && <ComponentDetail component={selected} />}</div>
        </div>
        {value && (
          <div className="chain-yaml-pane">
            <div className="chain-head">
              <span className="field-hint">{value.file}</span>
              <span className="chain-head-spacer" />
              <button className="btn btn-ghost" disabled={!dirty} onClick={() => setDraft(null)}>
                Revert
              </button>
              <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
                <Check size={14} />
                Save
              </button>
              {message && <span className="save-hint">{message}</span>}
            </div>
            <Tabs
              tabs={[
                { id: "yaml", label: "yaml" },
                { id: "diff", label: "diff vs saved" },
              ]}
              value={tab}
              onChange={(id) => setTab(id as "yaml" | "diff")}
            />
            {tab === "yaml" ? (
              <textarea
                aria-label="library yaml"
                className="input mono template-yaml"
                spellCheck={false}
                value={text}
                onChange={(e) => setDraft(e.target.value)}
              />
            ) : (
              <DraftDiff before={saved} after={text} />
            )}
          </div>
        )}
      </div>
    </>
  );
}
