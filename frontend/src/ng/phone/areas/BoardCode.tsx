import type { BoardOpenIn, Theme } from "../../../types";
import { DEFAULT_PREFS, type DiffPrefs } from "../../review/prefs";
import { DIFF_COLOURS, LAYOUTS, OPEN_IN, SCHEME_NOTE, SCHEMES, TOGGLES, type SchemeMode } from "../../settings/boardCodeWords";
import { SAVED } from "../../settings/savedWords";
import { useTheme } from "../../settings/useTheme";
import { title } from "../../theme/looks";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";
import { yamlOf } from "./yaml";
import { ADDED_AND_REMOVED_COLOURS } from "../../settings/sharedWords";

type Scheme = NonNullable<Theme["code_scheme"]>;
const label = (list: { value: string; label: string }[], v: string) => list.find((x) => x.value === v)?.label ?? v;

/** `/settings/board-and-code`: the code and board choices that were on Appearance, the same keys and the same words as the desktop's page. Save on change, as there. The default editor is a computer's choice, so it stays off the phone. */
export function BoardCodeScreen() {
  const { theme, error, save } = useTheme();
  const { edit, node } = useEditor();

  if (!theme) return <AreaScreen title="Board & code" status={{ label: SAVED.change }} yaml={false}>{error ? <p className="ph-error" role="alert">{error}</p> : <p className="ph-empty">Loading…</p>}</AreaScreen>;

  const scheme: Scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff: DiffPrefs = theme.diff ?? DEFAULT_PREFS;
  const choose = (name: string, value: string, options: { value: string; label: string }[], patchOf: (v: string) => Partial<Theme>): (() => void) => () =>
    edit({ kind: "choice", title: name, value, options, set: (v) => save(patchOf(v)) });
  const setDiff = (patch: Partial<DiffPrefs>) => void save({ diff: { ...diff, ...patch } });

  return (
    <AreaScreen title="Board & code" sub="Each choice applies at once." status={{ label: SAVED.change }} yaml={yamlOf(theme as unknown as Record<string, unknown>)}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      <Group
        title="Syntax highlighting"
        note={SCHEME_NOTE}
        rows={(["light", "dark"] as SchemeMode[]).map((m): RowSpec => ({ key: m, label: `${title(m)} scheme`, value: label(SCHEMES[m], scheme[m]), onEdit: choose(`${title(m)} scheme`, scheme[m], SCHEMES[m], (v) => ({ code_scheme: { ...scheme, [m]: v } as Scheme })) }))}
      />
      <Group
        title="Review diff"
        rows={[
          { label: "Layout", value: label(LAYOUTS, diff.layout), onEdit: choose("Layout", diff.layout, LAYOUTS, (v) => ({ diff: { ...diff, layout: v as DiffPrefs["layout"] } })) },
          { label: ADDED_AND_REMOVED_COLOURS, value: label(DIFF_COLOURS, diff.colours), onEdit: choose(ADDED_AND_REMOVED_COLOURS, diff.colours, DIFF_COLOURS.map((c) => ({ value: c.value, label: c.label, hint: c.note })), (v) => ({ diff: { ...diff, colours: v as DiffPrefs["colours"] } })) },
          ...TOGGLES.map((t): RowSpec => ({ key: t.key, label: t.label, sub: t.sub, sw: diff[t.key], onSwitch: (on) => setDiff({ [t.key]: on }) })),
        ]}
      />
      <Group
        title="Board"
        rows={[
          { label: "Open items in", value: label(OPEN_IN, theme.board.open_in), sub: "What a tap on a board row does on a computer; the phone always opens the item.", onEdit: choose("Open items in", theme.board.open_in, OPEN_IN, (v) => ({ board: { ...theme.board, open_in: v as BoardOpenIn } })) },
        ]}
      />
      {node}
    </AreaScreen>
  );
}
