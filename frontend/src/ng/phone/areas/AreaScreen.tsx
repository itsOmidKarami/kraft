import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { detailOf } from "../../http";
import { showToast } from "../../ui/Toast";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import type { Problem } from "../../templates/draft/types";
import { counts } from "../../templates/draft/view";
import { problemText } from "../../templates/problems";
import { Button } from "../../ui/Button";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { ScreenHeader } from "../nav/ScreenHeader";
import "./areas.css";

/** What an area screen is made of (W17 brief K.2): the Back header with the YAML toggle, the title with its status chip, the content, and, when the area has a draft with changes, the bar that reviews and publishes it. */
export function AreaScreen({ title, sub, draft, status, yaml = true, children }: {
  title: string;
  sub?: string;
  /** The area's draft: the dirty bar, Review & publish and the YAML view read it. Absent on a save-on-change area. */
  draft?: ConfigDraft;
  /** A chip that is not the draft's (`saved on change`). */
  status?: { label: string; tone?: "warn" | "bad" };
  /** The YAML toggle, on by default; the text is the draft's files, else `yamlText`. */
  yaml?: boolean | string;
  children: ReactNode;
}) {
  const [params, setParams] = useSearchParams();
  const sheet = useSheet();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const view = draft?.view ?? null;
  const n = view ? counts(view.result) : { changes: 0, problems: 0 };
  const dirty = !!view?.draft && n.changes > 0;
  const showYaml = params.get("yaml") === "1";
  const chip = status ?? (view ? (dirty ? { label: `draft · ${n.changes} change${n.changes === 1 ? "" : "s"}`, tone: "warn" as const } : n.problems ? { label: `${n.problems} problem${n.problems === 1 ? "" : "s"}`, tone: "bad" as const } : { label: "published" }) : undefined);

  const publish = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    const a = await draft.publish();
    setBusy(false);
    if (a.status === 200) {
      showToast("Published. Applies without a restart.");
      return sheet.close();
    }
    if (a.status === 409) return; // `draft.stale` shows the files; the person decides
    setError(detailOf(a.body));
  };
  const keepMine = async () => {
    if (!draft) return;
    setBusy(true);
    const a = await draft.keepMine();
    setBusy(false);
    if (a.status === 200) {
      showToast("Published over the newer version.");
      sheet.close();
    } else setError("The server refused to publish.");
  };
  const discard = async () => {
    if (!draft) return;
    setBusy(true);
    const a = await draft.discard();
    setBusy(false);
    if (a.status === 204 || a.status === 404) {
      showToast("Draft discarded.");
      sheet.close();
    } else setError("The server refused to discard the draft.");
  };
  const stale = draft?.stale ?? null;
  const problems: Problem[] = view?.result.problems ?? [];

  return (
    <>
      <ScreenHeader
        trailing={yaml !== false && (
          <button type="button" className="ph-yaml-btn" aria-pressed={showYaml} onClick={() => { const next = new URLSearchParams(params); if (showYaml) next.delete("yaml"); else next.set("yaml", "1"); setParams(next, { replace: true }); }}>YAML</button>
        )}
      />
      <div className="ph-content">
        <div className="ph-area-head">
          <div className="ph-area-title">
            <h1>{title}</h1>
            {chip && <span className={`ph-status${chip.tone ? ` ph-tone-${chip.tone}` : ""}`}>{chip.label}</span>}
          </div>
          {sub && <p className="ph-area-sub">{sub}</p>}
        </div>
        {draft?.error && <p className="ph-error" role="alert">{draft.error}</p>}
        {showYaml && yaml !== false ? (
          <div className="ph-yaml">
            {view?.result.yaml_error && <p className="ph-error" role="alert">{view.result.yaml_error.file}, line {view.result.yaml_error.line}: {view.result.yaml_error.message}</p>}
            {view && Object.entries(view.files).map(([file, text]) => (
              <div key={file}>
                <p className="ph-yaml-file">{file}</p>
                <pre className="ph-yaml-text">{text ?? "(deleted by this draft)"}</pre>
              </div>
            ))}
            {typeof yaml === "string" && <pre className="ph-yaml-text">{yaml}</pre>}
          </div>
        ) : children}
      </div>
      {dirty && (
        <div className="ph-dirtybar">
          <Button className="ph-btn" onClick={() => sheet.open("discard")}>Discard</Button>
          <Button className="ph-btn ph-btn-primary" variant="primary" onClick={() => sheet.open("review")}>Review &amp; publish</Button>
        </div>
      )}
      {sheet.is("review") && view && (
        <ConfirmSheet
          title="Review & publish"
          text={stale ? "This changed since you started. Publishing now would overwrite it, so nothing was written; your draft is kept." : problems.length || view.result.yaml_error ? "Fix the problems first: publishing waits for none." : "Applies without a restart once published."}
          busy={busy}
          error={error}
          confirm={{ label: stale ? "Keep my version and publish" : "Publish", disabled: !stale && (problems.length > 0 || !!view.result.yaml_error), run: stale ? keepMine : publish }}
          onClose={sheet.close}
        >
          {stale && <p className="ph-note">{Object.keys(stale.files).join(", ")} changed on disk after this draft began.</p>}
          {(problems.length > 0 || view.result.yaml_error) && (
            <div className="ph-review-lines" aria-label="Problems">
              {view.result.yaml_error && <div className="ph-problem"><span className="ph-problem-path">{view.result.yaml_error.file}, line {view.result.yaml_error.line}</span><span>{view.result.yaml_error.message}</span></div>}
              {problems.map((p, i) => <div key={i} className="ph-problem"><span className="ph-problem-path">{p.path}{p.field && p.field !== p.path ? ` · ${p.field}` : ""}</span><span>{problemText(p)}</span></div>)}
            </div>
          )}
          <div className="ph-review-lines" aria-label="Changes">
            {view.result.changes.map((c, i) => (
              <div key={i} className="ph-review-line">
                <span className={`ph-review-sign ${c.kind === "add" ? "ph-add" : c.kind === "remove" ? "ph-del" : "ph-chg"}`} aria-label={c.kind}>{c.kind === "add" ? "+" : c.kind === "remove" ? "−" : "~"}</span>
                <span className="ph-review-text"><span className="ph-mono">{c.path}</span> {c.summary}</span>
              </div>
            ))}
          </div>
        </ConfirmSheet>
      )}
      {sheet.is("discard") && (
        <ConfirmSheet title="Discard this draft?" text="Every change you made here is lost. It can't be undone." busy={busy} error={error} confirm={{ label: "Discard draft", danger: true, run: discard }} onClose={sheet.close} />
      )}
    </>
  );
}
