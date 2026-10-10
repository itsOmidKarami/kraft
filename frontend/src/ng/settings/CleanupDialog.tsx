import { useEffect, useRef, useState } from "react";
import { plural } from "../../format";
import { humanSize } from "../../sizes";
import type { StoragePreview, StoragePreviewItem, StorageUsage } from "../../types";
import { sendBulk, useBulk } from "../board/bulk";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

/** Where usage lands against the quota and the limit. `state_after` is null without a limit. */
function lands(p: StoragePreview, u: StorageUsage): string {
  const after = `${humanSize(p.used_after_bytes)} used afterwards`;
  const quota = humanSize(u.quota_bytes ?? 0);
  const limit = humanSize(u.limit_bytes ?? 0);
  if (p.state_after === "ok") return `${after}, under the quota of ${quota}.`;
  if (p.state_after === "over_quota") return `${after}: still over the quota of ${quota}, under the limit of ${limit}.`;
  if (p.state_after === "held") return `${after}: still over the limit of ${limit}, so starts stay held.`;
  return `${after}.`;
}

const lost = (i: StoragePreviewItem) =>
  i.uncommitted_files == null ? "no worktree" : i.uncommitted_files === 0 ? "no uncommitted files" : `${plural(i.uncommitted_files, "uncommitted file")} will be lost`;

/** The confirmation behind Clean up (Settings › Storage): it asks the server
 *  what archiving `ids` would do, shows that, and archives only on confirm.
 *  With no preview there is nothing to confirm, so the button stays off.
 *  `onDone` runs once the archive has answered, whatever it answered, so the
 *  page behind can read again. */
export function CleanupDialog({ ids, usage, onClose, onDone }: { ids: string[]; usage: StorageUsage; onClose: () => void; onDone: () => void }) {
  const [preview, setPreview] = useState<StoragePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; title: string; error: string }[] | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let live = true;
    void request<StoragePreview>("/storage/preview", jsonBody("POST", { ids })).then((r) => {
      if (!live) return;
      if (r.status === 200) setPreview(r.body);
      else setError(detailOf(r.body));
    });
    return () => { live = false; };
  }, [ids]);
  // Cancel unmounts when something failed: focus would fall to the page behind.
  useEffect(() => { if (failed) closeRef.current?.focus(); }, [failed]);

  const take = preview?.items.filter((i) => i.archivable) ?? [];
  const skipped = preview?.items.filter((i) => !i.archivable) ?? [];

  const confirm = async () => {
    setBusy(true);
    const out = await sendBulk("archive", take.map((i) => i.id));
    // This dialog names the failures; the board's bar would repeat them on the next visit.
    // Only this call's own answer: a board failure nobody has read yet stays.
    if (useBulk.getState().last === out) useBulk.getState().set(null);
    setBusy(false);
    onDone();
    // An id the server left out of its answer was not archived either.
    const archived = new Set("results" in out ? out.results.filter((r) => r.ok).map((r) => r.id) : []);
    const why = new Map("results" in out ? out.results.map((r) => [r.id, r.error]) : []);
    const bad = take.filter((i) => !archived.has(i.id)).map((i) => ({ id: i.id, title: i.title, error: ("error" in out ? out.error : why.get(i.id)) ?? "not archived" }));
    if (!bad.length) return onClose();
    setFailed(bad);
  };

  return (
    <Dialog
      title="Clean up worktrees"
      onClose={onClose}
      dirty={busy}
      className="set-st-dlg"
      footer={
        failed ? (
          <Button ref={closeRef} data-autofocus onClick={onClose}>Close</Button>
        ) : (
          <>
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="danger" disabled={!take.length || busy} onClick={() => void confirm()}>
              Archive {plural(take.length, "item")} and free {humanSize(preview?.freed_bytes ?? 0)}
            </Button>
          </>
        )
      }
    >
      {error && <p className="set-error" role="alert">Could not check what this would remove, so nothing was removed: {error}</p>}
      {!preview && !error && <p className="set-hint">Checking what removing these would do…</p>}
      {preview && !failed && (
        <>
          <p className="set-st-sum"><b>Frees {humanSize(preview.freed_bytes)}.</b> {lands(preview, usage)}</p>
          <ul className="set-st-list">
            {take.map((i) => (
              <li key={i.id} className="set-st-li">
                <span className="set-st-li-title">{i.title} <span className="set-mono">{humanSize(i.bytes)}</span></span>
                <span className="set-hint">{lost(i)} · {i.branch_kept ? "branch stays: it has commits nothing else holds" : "branch is deleted"}</span>
              </li>
            ))}
            {skipped.map((i) => (
              <li key={i.id} className="set-st-li">
                <span className="set-st-li-title">{i.title}</span>
                <span className="set-hint">Skipped: {i.refusal ?? "not archivable"}</span>
              </li>
            ))}
          </ul>
          <p className="set-hint">Each item moves to Archived. You can restore it there as a record; its worktree is not brought back.</p>
        </>
      )}
      {failed && (
        <>
          <p className="set-error" role="alert">{take.length - failed.length} of {take.length} archived. These were not:</p>
          <ul className="set-st-list">
            {failed.map((f) => <li key={f.id} className="set-st-li">{f.title}: {f.error}</li>)}
          </ul>
        </>
      )}
    </Dialog>
  );
}
