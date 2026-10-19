import type { Accent, ColourAmount, Surface, Theme, ThemeMode } from "../../types";
import { accentOf, ACCENTS, AMOUNTS, resolveMode, SURFACES, title } from "../theme/looks";
import { ThemeCard } from "../theme/ThemeCard";
import { Segmented } from "../ui/Segmented";
import { themeYaml, useTheme } from "./useTheme";
import { YamlFrame } from "./YamlFrame";
import "./settings.css";
import { MONO_HAS_NO_ACCENT, ACCENT_LINKS_ONLY } from "./sharedWords";

const DENSITIES: { value: Theme["density"]; label: string }[] = [
  { value: "compact", label: "Compact" },
  { value: "comfortable", label: "Comfortable" },
];
const MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/** Settings › Appearance (UX V2 W1): how Kraft looks. The code and board preferences are on Board & code. */
export function AppearancePage() {
  const { theme, error, save } = useTheme();

  if (!theme) return <div className="ng-settings">{error && <p role="alert">{error}</p>}</div>;

  const surface = theme.surface ?? "ink";
  const amount = theme.colour_amount ?? "full";
  const accent = amount === "mono" ? "none" : accentOf(theme.accent);
  const mode = resolveMode(theme.mode);

  const setAmount = (v: ColourAmount) => save(v === "mono" ? { colour_amount: v, accent: "none" } : { colour_amount: v });

  return (
    <YamlFrame pageKey="appearance" file="theme.yaml" title="appearance" icon="palette" yaml={themeYaml(theme)}>
      <div className="set-page">
        <div className="set-title"><h1>Appearance</h1><p className="lede">Each choice applies at once.</p></div>
        {theme.derived && <p className="note">These are Kraft's default colours until you change one.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="appearance-cols">
          <div className="appearance-controls">
            <section>
              <h2 className="set-h2">Mode</h2>
              <Segmented label="Mode" options={MODES} value={theme.mode} onChange={(v) => save({ mode: v })} />
            </section>
            <section>
              <h2 className="set-h2">Colour amount</h2>
              <Segmented label="Colour amount" options={AMOUNTS} value={amount} onChange={setAmount} />
              <p className="note">Scales the tint of the surfaces, the accent and the status colours together. Mono leaves only grey.</p>
            </section>
            <section>
              <h2 className="set-h2">Surface</h2>
              <div className="surface-grid">
                {SURFACES.map((s) => (
                  <button key={s.id} type="button" className="surface-choice" aria-pressed={surface === s.id} onClick={() => save({ surface: s.id as Surface })}>
                    <span className="surface-swatch" data-surface={s.id} data-mode={mode} data-amount={amount} />
                    <span className="choice-name">{s.name}</span>
                    <span className="choice-note">{s.note}</span>
                  </button>
                ))}
              </div>
            </section>
            <section>
              <h2 className="set-h2">Accent</h2>
              <div className="accent-row">
                {ACCENTS.map((a) => (
                  <button key={a} type="button" className="accent-choice" aria-pressed={accent === a} disabled={amount === "mono"} onClick={() => save({ accent: a as Accent })}>
                    <span className="accent-dot" data-accent={a} data-mode={mode} data-amount="subtle" />
                    <span className="choice-name">{title(a)}</span>
                  </button>
                ))}
              </div>
              <p className="note">{amount === "mono" ? MONO_HAS_NO_ACCENT : ACCENT_LINKS_ONLY}</p>
            </section>
            <section>
              <h2 className="set-h2">Density</h2>
              <Segmented label="Density" options={DENSITIES} value={theme.density} onChange={(v) => save({ density: v })} />
              <p className="note">Compact is the default. This interface is dense on purpose. Comfortable adds 2px to row padding and 1px to body type.</p>
            </section>
          </div>
          <section className="appearance-preview">
            <h2 className="set-h2">Preview</h2>
            <ThemeCard surface={surface} accent={accent} amount={amount} mode={mode} />
            <p className="caption">{`${title(surface)} · ${accent} accent · ${mode} · ${amount}`}</p>
          </section>
        </div>
      </div>
    </YamlFrame>
  );
}
