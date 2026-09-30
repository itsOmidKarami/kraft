import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Accent, ColourAmount, Surface, Theme, ThemeMode } from "../../types";
import { applyTheme, lookOf } from "../theme/applyTheme";
import { ACCENTS, AMOUNTS, resolveMode, SURFACES, title } from "../theme/looks";
import { ThemeCard } from "../theme/ThemeCard";
import { Segmented } from "../ui/Segmented";
import "./settings.css";

const MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/** Settings › Appearance, colour section (UX V2 W1). Saved on change: each
 *  control sends only its own key, and the server merges it into theme.yaml. */
export function AppearancePage() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getTheme().then(setTheme, (e) => setError(String(e instanceof Error ? e.message : e)));
  }, []);
  // The page paints whatever it shows: a change at once, a failed save undone.
  useEffect(() => {
    if (theme) applyTheme(lookOf(theme));
  }, [theme]);

  if (!theme) return <main className="ng-settings">{error && <p role="alert">{error}</p>}</main>;

  const surface = theme.surface ?? "graphite";
  const amount = theme.colour_amount ?? "subtle";
  const accent = amount === "mono" ? "none" : (theme.accent ?? "none");
  const mode = resolveMode(theme.mode);

  const save = async (patch: Partial<Theme>) => {
    // A derived look is written out whole on its first change, so the other
    // two keys stop following `palette` at the same moment (brief E.3).
    const body = theme.derived ? { surface, accent, colour_amount: amount, ...patch } : patch;
    const before = theme;
    const next = { ...theme, ...body, derived: false };
    setTheme(next);
    setError(null);
    try {
      const saved = await api.putTheme(body);
      setTheme(saved);
    } catch (e) {
      setTheme(before);
      setError(`Not saved: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const setAmount = (v: ColourAmount) => save(v === "mono" ? { colour_amount: v, accent: "none" } : { colour_amount: v });

  return (
    <main className="ng-settings">
      <header className="ng-settings-head">
        <span className="crumbs">Settings › Appearance</span>
        <span className="saved-note">saved on change</span>
      </header>
      <div className="appearance">
        <h1>Appearance</h1>
        <p className="lede">The choice applies at once and is kept in theme.yaml.</p>
        {theme.derived && <p className="note">These follow the {theme.palette} palette until you change one.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="appearance-cols">
          <div className="appearance-controls">
            <section>
              <h2>Mode</h2>
              <Segmented label="Mode" options={MODES} value={theme.mode} onChange={(v) => save({ mode: v })} />
            </section>
            <section>
              <h2>Colour amount</h2>
              <Segmented label="Colour amount" options={AMOUNTS} value={amount} onChange={setAmount} />
              <p className="note">Scales the tint of the surfaces, the accent and the status colours together. Mono leaves only grey.</p>
            </section>
            <section>
              <h2>Surface</h2>
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
              <h2>Accent</h2>
              <div className="accent-row">
                {ACCENTS.map((a) => (
                  <button key={a} type="button" className="accent-choice" aria-pressed={accent === a} disabled={amount === "mono"} onClick={() => save({ accent: a as Accent })}>
                    <span className="accent-dot" data-accent={a} data-mode={mode} data-amount="subtle" />
                    <span className="choice-name">{title(a)}</span>
                  </button>
                ))}
              </div>
              <p className="note">{amount === "mono" ? "Mono has no accent. Pick Subtle or Full to choose one." : "The accent colours links only. Selection, buttons and focus stay neutral."}</p>
            </section>
          </div>
          <section className="appearance-preview">
            <h2>Preview</h2>
            <ThemeCard surface={surface} accent={accent} amount={amount} mode={mode} />
            <p className="caption">{`${title(surface)} · ${accent} accent · ${mode} · ${amount}`}</p>
          </section>
        </div>
      </div>
    </main>
  );
}
