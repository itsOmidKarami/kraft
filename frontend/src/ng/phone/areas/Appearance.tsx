import { useEffect, useState } from "react";
import * as api from "../../../api";
import type { Accent, BoardOpenIn, ColourAmount, Surface, Theme, ThemeMode } from "../../../types";
import { applyTheme, lookOf } from "../../theme/applyTheme";
import { ACCENTS, AMOUNTS, resolveMode, SURFACES, title } from "../../theme/looks";
import { DEFAULT_PREFS, type DiffPrefs } from "../../review/prefs";
import { AreaScreen } from "./AreaScreen";
import type { Option } from "../nav/Sheet";
import { Group, useEditor, type RowSpec } from "./kit";
import { yamlOf } from "./yaml";

type Scheme = NonNullable<Theme["code_scheme"]>;
const MODES: { value: ThemeMode; label: string }[] = [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }];
const SCHEMES = {
  light: [{ value: "auto", label: "Auto" }, { value: "none", label: "None" }, { value: "solarized-light", label: "Solarized Light" }],
  dark: [{ value: "auto", label: "Auto" }, { value: "none", label: "None" }, { value: "solarized-dark", label: "Solarized Dark" }, { value: "monokai", label: "Monokai" }, { value: "dracula", label: "Dracula" }],
};
const DIFF_COLOURS: { value: DiffPrefs["colours"]; label: string; hint: string }[] = [
  { value: "theme", label: "Theme", hint: "status colours" },
  { value: "safe", label: "Colour-blind safe", hint: "blue, orange" },
  { value: "plain", label: "None", hint: "marks only" },
];
const TOGGLES: { key: "show_whitespace" | "word_highlight" | "wrap_lines" | "one_file_at_a_time"; label: string; sub: string }[] = [
  { key: "show_whitespace", label: "Show whitespace changes", sub: "Off hides lines that differ only in whitespace" },
  { key: "word_highlight", label: "Highlight changed words", sub: "A stronger tint on the words that changed inside a line" },
  { key: "wrap_lines", label: "Wrap long lines", sub: "Off scrolls sideways" },
  { key: "one_file_at_a_time", label: "One file at a time", sub: "The tree stays beside a single file" },
];
const label = (list: { value: string; label: string }[], v: string) => list.find((x) => x.value === v)?.label ?? v;

/** `/settings/appearance` (W17 brief O.4). Save on change: each control sends only its own key through `putTheme`, which merges it into theme.yaml; the page repaints at once and a refused save puts the control back. */
export function AppearanceScreen() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { edit, node } = useEditor();
  useEffect(() => {
    api.getTheme().then(setTheme, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => {
    if (theme) applyTheme(lookOf(theme));
  }, [theme]);

  if (!theme) return <AreaScreen title="Appearance" status={{ label: "saved on change" }} yaml={false}>{error ? <p className="ph-error" role="alert">{error}</p> : <p className="ph-empty">Loading…</p>}</AreaScreen>;

  const surface = theme.surface ?? "graphite";
  const amount = theme.colour_amount ?? "subtle";
  const accent = amount === "mono" ? "none" : (theme.accent ?? "none");
  const mode = resolveMode(theme.mode);
  const scheme: Scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff: DiffPrefs = theme.diff ?? DEFAULT_PREFS;

  const save = async (patch: Partial<Theme>): Promise<string | null> => {
    // A derived look is written out whole on its first change, so the other two keys stop following `palette` at the same moment.
    const body = theme.derived ? { surface, accent, colour_amount: amount, ...patch } : patch;
    const before = theme;
    setTheme({ ...theme, ...body, derived: false });
    setError(null);
    try {
      setTheme(await api.putTheme(body));
      return null;
    } catch (e) {
      setTheme(before);
      const why = e instanceof Error ? e.message : String(e);
      setError(`Not saved: ${why}`);
      return why;
    }
  };
  const choose = (title: string, value: string, options: Option[], patchOf: (v: string) => Partial<Theme>): (() => void) => () =>
    edit({ kind: "choice", title, value, options, set: (v) => save(patchOf(v)) });
  const setDiff = (patch: Partial<DiffPrefs>) => void save({ diff: { ...diff, ...patch } });

  return (
    <AreaScreen title="Appearance" sub="The choice applies at once and is kept in theme.yaml." status={{ label: "saved on change" }} yaml={yamlOf(theme as unknown as Record<string, unknown>)}>
      {theme.derived && <p className="ph-note">These follow the {theme.palette} palette until you change one.</p>}
      {error && <p className="ph-error" role="alert">{error}</p>}
      <Group
        title="Theme"
        rows={[
          { label: "Mode", value: label(MODES, theme.mode), onEdit: choose("Mode", theme.mode, MODES, (v) => ({ mode: v as ThemeMode })) },
          { label: "Colour amount", value: label(AMOUNTS, amount), sub: "Scales the tint of the surfaces, the accent and the status colours together. Mono leaves only grey.", onEdit: choose("Colour amount", amount, AMOUNTS, (v) => (v === "mono" ? { colour_amount: v as ColourAmount, accent: "none" } : { colour_amount: v as ColourAmount })) },
        ]}
      />
      <Group
        title="Surface"
        rows={SURFACES.map((s): RowSpec => ({ key: s.id, label: s.name, sub: s.note, selected: surface === s.id, lead: <span className="ph-swatch" data-surface={s.id} data-mode={mode} data-amount={amount} aria-hidden="true" />, onClick: () => void save({ surface: s.id as Surface }) }))}
      />
      <Group
        title="Accent"
        foot={amount === "mono" ? "Mono has no accent. Pick Subtle or Full to choose one." : "The accent colours links only. Selection, buttons and focus stay neutral."}
        rows={ACCENTS.map((a): RowSpec => ({ key: a, label: title(a), selected: accent === a, disabled: amount === "mono", lead: <span className="ph-accent-dot" data-accent={a} data-mode={mode} data-amount="subtle" aria-hidden="true" />, onClick: () => void save({ accent: a as Accent }) }))}
      />
      <Group
        title="Syntax highlighting"
        note="Colours code in review diffs, chosen separately for light and dark. Auto follows Colour amount; None is a single colour."
        rows={(["light", "dark"] as const).map((m): RowSpec => ({ key: m, label: `${title(m)} scheme`, value: label(SCHEMES[m], scheme[m]), onEdit: choose(`${title(m)} scheme`, scheme[m], SCHEMES[m], (v) => ({ code_scheme: { ...scheme, [m]: v } as Scheme })) }))}
      />
      <Group
        title="Review diff"
        rows={[
          { label: "Layout", value: diff.layout === "split" ? "Side-by-side" : "Inline", onEdit: () => edit({ kind: "choice", title: "Layout", value: diff.layout, options: [{ value: "split", label: "Side-by-side" }, { value: "unified", label: "Inline" }], set: (v) => save({ diff: { ...diff, layout: v as DiffPrefs["layout"] } }) }) },
          { label: "Added and removed colours", value: label(DIFF_COLOURS, diff.colours), onEdit: () => edit({ kind: "choice", title: "Added and removed colours", value: diff.colours, options: DIFF_COLOURS, set: (v) => save({ diff: { ...diff, colours: v as DiffPrefs["colours"] } }) }) },
          ...TOGGLES.map((t): RowSpec => ({ key: t.key, label: t.label, sub: t.sub, sw: diff[t.key], onSwitch: (on) => setDiff({ [t.key]: on }) })),
        ]}
      />
      <Group
        title="Board"
        rows={[
          { label: "Density", value: theme.density === "comfortable" ? "Comfortable" : "Compact", sub: "Compact is the default. Comfortable adds 2px to row padding and 1px to body type.", onEdit: choose("Density", theme.density, [{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }], (v) => ({ density: v as Theme["density"] })) },
          { label: "Open items in", value: theme.board.open_in === "full" ? "Full page" : "Peek", sub: "What a tap on a board row does on a computer; the phone always opens the item.", onEdit: choose("Open items in", theme.board.open_in, [{ value: "peek", label: "Peek" }, { value: "full", label: "Full page" }], (v) => ({ board: { ...theme.board, open_in: v as BoardOpenIn } })) },
        ]}
      />
      {node}
    </AreaScreen>
  );
}
