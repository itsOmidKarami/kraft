import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Accent, ColourAmount, Surface, Theme, ThemeMode } from "../../types";
import { applyTheme, lookOf } from "../theme/applyTheme";
import { ACCENTS, AMOUNTS, resolveMode, SURFACES, title } from "../theme/looks";
import { ThemeCard } from "../theme/ThemeCard";
import { DEFAULT_PREFS } from "../review/prefs";
import { Head, Kv, Note } from "../templates/panes/controls";
import { Segmented } from "../ui/Segmented";
import { AppearanceMore } from "./AppearanceMore";
import { YamlFrame } from "./YamlFrame";
import "./settings.css";

const MODES: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/** What theme.yaml holds, from the page's own state: the effective value of every control. */
function themeYaml(theme: Theme): string {
  const scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff = theme.diff ?? DEFAULT_PREFS;
  const amount = theme.colour_amount ?? "subtle";
  return [
    `mode: ${theme.mode}`,
    `surface: ${theme.surface ?? "graphite"}`,
    `accent: ${amount === "mono" ? "none" : (theme.accent ?? "none")}`,
    `colour_amount: ${amount}`,
    "code_scheme:",
    `  light: ${scheme.light}`,
    `  dark: ${scheme.dark}`,
    "diff:",
    `  layout: ${diff.layout}`,
    `  colours: ${diff.colours}`,
    `  show_whitespace: ${diff.show_whitespace}`,
    `  word_highlight: ${diff.word_highlight}`,
    `  wrap_lines: ${diff.wrap_lines}`,
    `  one_file_at_a_time: ${diff.one_file_at_a_time}`,
    `density: ${theme.density}`,
    "board:",
    `  open_in: ${theme.board.open_in}`,
  ].join("\n");
}

function Overview({ theme }: { theme: Theme }) {
  const scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff = theme.diff ?? DEFAULT_PREFS;
  const amount = theme.colour_amount ?? "subtle";
  const mode = resolveMode(theme.mode);
  return (
    <>
      <Head>Theme</Head>
      <Kv k="mode" v={theme.mode} mono />
      <Kv k="surface" v={theme.surface ?? "graphite"} mono />
      <Kv k="accent" v={amount === "mono" ? "none" : (theme.accent ?? "none")} mono />
      <Kv k="colour amount" v={amount} mono />
      <Kv k="code scheme" v={`${scheme[mode]} (${mode})`} mono />
      <Kv k="density" v={theme.density} mono />
      <Head>Review diff</Head>
      <Kv k="layout" v={diff.layout} mono />
      <Kv k="colours" v={diff.colours} mono />
      <Kv k="whitespace" v={diff.show_whitespace ? "shown" : "hidden"} mono />
      <Kv k="changed words" v={diff.word_highlight ? "highlighted" : "off"} mono />
      <Kv k="long lines" v={diff.wrap_lines ? "wrapped" : "scroll"} mono />
      <Kv k="files" v={diff.one_file_at_a_time ? "one at a time" : "all"} mono />
      <Note>The theme is also kept in this browser, so the page paints in the right colours before the server answers. System follows the operating system and changes with it.</Note>
    </>
  );
}

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

  if (!theme) return <div className="ng-settings">{error && <p role="alert">{error}</p>}</div>;

  const surface = theme.surface ?? "graphite";
  const amount = theme.colour_amount ?? "subtle";
  const accent = amount === "mono" ? "none" : (theme.accent ?? "none");
  const mode = resolveMode(theme.mode);

  const save = async (patch: Partial<Theme>) => {
    // A derived look is written out whole on its first change, so the other
    // two keys stop following the default at the same moment (brief E.3).
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
    <YamlFrame pageKey="appearance" file="theme.yaml" title="appearance" icon="palette" yaml={themeYaml(theme)} overview={<Overview theme={theme} />}>
      <div className="appearance">
        <h1>Appearance</h1>
        <p className="lede">The choice applies at once and is kept in theme.yaml.</p>
        {theme.derived && <p className="note">These are Kraft's default colours until you change one.</p>}
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
        <AppearanceMore theme={theme} save={save} />
      </div>
    </YamlFrame>
  );
}
