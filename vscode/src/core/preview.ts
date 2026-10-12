/** What previewOf reads off a tab: its label, and a webview tab's viewType. */
export type TabLike = { label: string; viewType?: string };

/** A gate document's preview: the item, and the basename that render used. */
export type ShownPreview = { id: string; basename: string };

/** The gate document a markdown preview tab shows, when Kraft opened it: the
 *  label ends with " " + a basename Kraft previewed (`opened`: basename → id),
 *  whatever the localized prefix (`Preview`, `[Preview]`, `Vorschau`). Any
 *  other tab, or another file whose name merely ends `(<id>).md`, is undefined,
 *  so no Approve binds to a render Kraft never showed. */
export function previewOf(tab: TabLike | undefined, opened: ReadonlyMap<string, string>): ShownPreview | undefined {
  if (!tab?.viewType?.endsWith("markdown.preview")) return undefined;
  let found: ShownPreview | undefined;
  for (const [basename, id] of opened)
    if (tab.label.endsWith(` ${basename}`) && basename.length > (found?.basename.length ?? -1)) found = { id, basename };
  return found;
}

/** Whether a preview shows an older render of its item than the latest one
 *  Kraft showed (`latest`: id → basename), e.g. the spec's tab still open once
 *  the plan gate's document is up. Approving there would send the newer
 *  render's digest, so it reads first instead. */
export const previewIsStale = (p: ShownPreview, latest: ReadonlyMap<string, string>): boolean => latest.get(p.id) !== p.basename;

/** An editor group as previewTarget reads it: whether it is active, and its active tab. */
export type GroupLike = { isActive: boolean; activeTab?: TabLike };

const isUri = (v: unknown): v is { scheme: string } => typeof (v as { scheme?: unknown } | undefined)?.scheme === "string";
const same = (a: ShownPreview | undefined, b: ShownPreview | undefined) => a?.id === b?.id && a?.basename === b?.basename;

/**
 * The preview a command acts on when its argument names no item. From the
 * palette (no argument): the active group's active tab. From a title bar
 * (a `webview-panel` uri): the group clicked, which the extension API cannot
 * name (VS Code's `{ groupId }` is an internal id no TabGroup exposes), so
 * every group whose active tab is a webview could be it; only when all of
 * them show the same Kraft preview is that the one. Any other argument (a
 * diff's uri, another group's file) is no preview.
 */
export function previewTarget(arg: unknown, groups: readonly GroupLike[], opened: ReadonlyMap<string, string>): ShownPreview | undefined {
  if (arg === undefined) return previewOf(groups.find((g) => g.isActive)?.activeTab, opened);
  if (!isUri(arg) || arg.scheme !== "webview-panel") return undefined;
  const found = groups.filter((g) => g.activeTab?.viewType !== undefined).map((g) => previewOf(g.activeTab, opened));
  return found.length && found.every((p) => same(p, found[0])) ? found[0] : undefined;
}

/** A title bar's click (a `webview-panel` uri) that previewTarget could not
 *  pin to one preview: another group's active tab is a webview too. The
 *  command would otherwise do nothing; it says this instead. */
export const previewUnresolved = (arg: unknown, target: ShownPreview | undefined): boolean =>
  target === undefined && isUri(arg) && arg.scheme === "webview-panel";

export const PREVIEW_UNRESOLVED =
  "Kraft can't tell which preview this is while another editor group shows a webview. Click in the preview, then run the command from the Command Palette.";

/** A gate command's call, normalised: the title bar passes `(uri, { groupId })`,
 *  so only a string is a gate or a note. `stale` when the item came from a
 *  preview of an older render than the latest (`latest`: id → basename). */
export function gateCall(direct: string | undefined, target: ShownPreview | undefined, latest: ReadonlyMap<string, string>, gate: unknown, note?: unknown):
  { id?: string; gate?: string; note?: string; stale: boolean } {
  const str = (v: unknown) => (typeof v === "string" ? v : undefined);
  const p = direct === undefined ? target : undefined;
  return { id: direct ?? p?.id, gate: str(gate), note: str(note), stale: p !== undefined && previewIsStale(p, latest) };
}
