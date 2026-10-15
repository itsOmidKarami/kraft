import { useCallback, useEffect, useState } from "react";
import { ago, plural } from "../../../format";
import { humanSize } from "../../../sizes";
import type { StoragePreview, StorageUsage } from "../../../types";
import { sendBulk } from "../../board/bulk";
import { detailOf, jsonBody, request } from "../../http";
import { CATEGORIES, cleanAll, COUNTED, HOUSEKEEPING, NOTHING_TO_CLEAN, storageNote } from "../../settings/storageWords";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { AreaScreen } from "./AreaScreen";
import { Group, type RowSpec } from "./kit";

/** `/settings/storage`: what the worktrees use against the quota and limit, and the one clean-up that archives every finished item's worktree. Picking items is on desktop. */
export function StorageScreen() {
  const [data, setData] = useState<StorageUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [measuring, setMeasuring] = useState(true);
  const [preview, setPreview] = useState<StoragePreview | null>(null);
  const [cleanError, setCleanError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sheet = useSheet();

  const load = useCallback(async (refresh: boolean) => {
    setMeasuring(true);
    const r = await request<StorageUsage>(`/storage${refresh ? "?refresh=1" : ""}`);
    setMeasuring(false);
    // A failed read keeps the last figures: they are older, not wrong.
    if (r.status !== 200) return setError(detailOf(r.body));
    setError(null);
    setData(r.body);
  }, []);
  useEffect(() => void load(false), [load]);

  const reclaimable = (data?.items ?? []).filter((i) => i.reclaimable);
  const used = data?.used_bytes ?? 0;
  const limit = data?.limit_bytes ?? null;
  const note = storageNote(data, measuring);
  const ask = async () => {
    setPreview(null);
    setCleanError(null);
    sheet.open("clean");
    const r = await request<StoragePreview>("/storage/preview", jsonBody("POST", { ids: reclaimable.map((i) => i.id) }));
    if (r.status === 200) setPreview(r.body);
    else setCleanError(detailOf(r.body));
  };
  const take = preview?.items.filter((i) => i.archivable) ?? [];
  const lost = take.reduce((n, i) => n + (i.uncommitted_files ?? 0), 0);
  const confirm = async () => {
    setBusy(true);
    const out = await sendBulk("archive", take.map((i) => i.id));
    setBusy(false);
    const failed = "error" in out ? out.error : out.results.find((r) => !r.ok)?.error;
    if (failed) return setCleanError(failed);
    sheet.close();
    void load(false);
  };

  const usage: RowSpec[] = !data ? [] : [
    { label: "counted", value: humanSize(used), sub: data.measured_at ? `${COUNTED}, measured ${ago(data.measured_at)}` : COUNTED },
    ...(limit != null && data.state ? [{ label: "quota", value: humanSize(data.quota_bytes ?? 0) } as RowSpec, { label: "limit", value: humanSize(limit), sub: "starts are held above it" } as RowSpec] : [{ label: "limit", value: "not set", to: HOUSEKEEPING, sub: "Set one in Policy › Housekeeping" } as RowSpec]),
    ...(limit != null && data.state ? [{ label: "edit the quota and limit", to: HOUSEKEEPING } as RowSpec] : []),
  ];
  return (
    <AreaScreen title="Storage" sub="What the worktrees use, and what removing the finished ones would free." yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {!data && measuring && <p className="ph-note">Measuring the run folder. This can take half a minute on a large instance.</p>}
      {note && <p className="ph-note" role="status">{note}</p>}
      {data && (
        <>
          <Group title="Usage" rows={[...usage, { label: "Refresh", onClick: () => void load(true), disabled: measuring, sub: "Measures again; this can take half a minute." }]} />
          <Group title="Where it goes" rows={CATEGORIES.map(([key, label]): RowSpec => ({ key, label, value: humanSize(data.categories?.[key] ?? 0) }))} />
          <Group
            title={`Worktrees · ${plural(data.items?.length ?? 0, "item")}`}
            rows={[
              { label: cleanAll(reclaimable.length, data.reclaimable_bytes), onClick: () => void ask(), disabled: !reclaimable.length, danger: true },
            ]}
            foot={reclaimable.length ? "Cleans up the finished items: they move to Archived and their worktrees are deleted. Choosing items one by one is on desktop." : NOTHING_TO_CLEAN}
          />
        </>
      )}
      {sheet.is("clean") && (
        <ConfirmSheet
          title="Clean up finished worktrees?"
          text={preview ? `${plural(take.length, "item")} cleaned up: moved to Archived, their worktrees deleted. ${lost ? `${plural(lost, "uncommitted file")} will be lost.` : "No uncommitted files."} ${humanSize(preview.used_after_bytes)} used afterwards.` : "Asking the server what this would do…"}
          confirm={{ label: "Clean up", danger: true, disabled: !take.length, run: () => void confirm() }}
          busy={busy || !preview}
          error={cleanError}
          onClose={sheet.close}
        />
      )}
    </AreaScreen>
  );
}
