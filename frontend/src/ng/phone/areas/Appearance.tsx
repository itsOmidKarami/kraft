import type { Accent, ColourAmount, Surface, Theme, ThemeMode } from "../../../types";
import { accentOf, ACCENTS, AMOUNTS, resolveMode, SURFACES, title } from "../../theme/looks";
import { SAVED } from "../../settings/savedWords";
import { useTheme } from "../../settings/useTheme";
import { AreaScreen } from "./AreaScreen";
import type { Option } from "../nav/Sheet";
import { Group, useEditor, type RowSpec } from "./kit";
import { yamlOf } from "./yaml";
import { MONO_HAS_NO_ACCENT, ACCENT_LINKS_ONLY } from "../../settings/sharedWords";

const MODES: { value: ThemeMode; label: string }[] = [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }];
const label = (list: { value: string; label: string }[], v: string) => list.find((x) => x.value === v)?.label ?? v;

/** `/settings/appearance` (W17 brief O.4): how Kraft looks; the code and board choices are `/settings/board-and-code`. Save on change: each control sends only its own key through `putTheme`, which merges it into theme.yaml; the page repaints at once and a refused save puts the control back. */
export function AppearanceScreen() {
  const { theme, error, save } = useTheme();
  const { edit, node } = useEditor();
  if (!theme) return <AreaScreen title="Appearance" status={{ label: SAVED.change }} yaml={false}>{error ? <p className="ph-error" role="alert">{error}</p> : <p className="ph-empty">Loading…</p>}</AreaScreen>;

  const surface = theme.surface ?? "ink";
  const amount = theme.colour_amount ?? "full";
  const accent = amount === "mono" ? "none" : accentOf(theme.accent);
  const mode = resolveMode(theme.mode);

  const choose = (title: string, value: string, options: Option[], patchOf: (v: string) => Partial<Theme>): (() => void) => () =>
    edit({ kind: "choice", title, value, options, set: (v) => save(patchOf(v)) });

  return (
    <AreaScreen title="Appearance" sub="Each choice applies at once." status={{ label: SAVED.change }} yaml={yamlOf(theme as unknown as Record<string, unknown>)}>
      {theme.derived && <p className="ph-note">These follow the {theme.palette} palette until you change one.</p>}
      {error && <p className="ph-error" role="alert">{error}</p>}
      <Group
        title="Theme"
        rows={[
          { label: "Mode", value: label(MODES, theme.mode), onEdit: choose("Mode", theme.mode, MODES, (v) => ({ mode: v as ThemeMode })) },
          { label: "Colour amount", value: label(AMOUNTS, amount), sub: "Scales the tint of the surfaces, the accent and the status colours together. Mono leaves only grey.", onEdit: choose("Colour amount", amount, AMOUNTS, (v) => (v === "mono" ? { colour_amount: v as ColourAmount, accent: "none" } : { colour_amount: v as ColourAmount })) },
          { label: "Density", value: theme.density === "comfortable" ? "Comfortable" : "Compact", sub: "Compact is the default. Comfortable adds 2px to row padding and 1px to body type.", onEdit: choose("Density", theme.density, [{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }], (v) => ({ density: v as Theme["density"] })) },
        ]}
      />
      <Group
        title="Surface"
        rows={SURFACES.map((s): RowSpec => ({ key: s.id, label: s.name, sub: s.note, selected: surface === s.id, lead: <span className="ph-swatch" data-surface={s.id} data-mode={mode} data-amount={amount} aria-hidden="true" />, onClick: () => void save({ surface: s.id as Surface }) }))}
      />
      <Group
        title="Accent"
        foot={amount === "mono" ? MONO_HAS_NO_ACCENT : ACCENT_LINKS_ONLY}
        rows={ACCENTS.map((a): RowSpec => ({ key: a, label: title(a), selected: accent === a, disabled: amount === "mono", lead: <span className="ph-accent-dot" data-accent={a} data-mode={mode} data-amount="subtle" aria-hidden="true" />, onClick: () => void save({ accent: a as Accent }) }))}
      />
      {node}
    </AreaScreen>
  );
}
