import type { BoardOpenIn, Theme } from "../../types";
import { DEFAULT_PREFS, type DiffPrefs } from "../review/prefs";
import { Segmented } from "../ui/Segmented";
import { editorName, useEditors } from "../item/editors";
import { Switch } from "../ui/Switch";
import { onMac } from "../keys";
import { DIFF_COLOURS, LAYOUTS, OPEN_IN, SCHEME_NOTE, SCHEMES, TOGGLES, type SchemeMode } from "./boardCodeWords";
import { themeYaml, useTheme } from "./useTheme";
import { YamlFrame } from "./YamlFrame";
import "./settings.css";
import { ADDED_AND_REMOVED_COLOURS } from "./sharedWords";

type Scheme = NonNullable<Theme["code_scheme"]>;

const SAMPLE: [string, string][][] = [
  [["com", "# find a work item"]],
  [["kw", "def "], ["name", "load"], ["", "(id, cache=None):"]],
  [["", "  "], ["kw", "return "], ["name", "cache"], ["", ".get(id, "], ["num", "0"], ["", ")"]],
  [["", "  "], ["str", '"missing"']],
];

/** Settings › Board & code: how Kraft behaves where you read code and work the board. Saved on change like
 *  Appearance, which these moved out of; the keys are the same. Each control sends its own key; `code_scheme`
 *  and `diff` go whole because `PUT /theme` merges top-level keys only. */
export function BoardCodePage() {
  const { theme, error, save } = useTheme();
  if (!theme) return <div className="ng-settings">{error && <p role="alert">{error}</p>}</div>;

  const scheme: Scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff: DiffPrefs = theme.diff ?? DEFAULT_PREFS;
  const surface = theme.surface ?? "graphite";
  const amount = theme.colour_amount ?? "subtle";
  const setDiff = (patch: Partial<DiffPrefs>) => save({ diff: { ...diff, ...patch } });
  const unified = diff.layout === "unified";

  const group = (mode: SchemeMode) => (
    <div className="set-code-group" role="group" aria-label={`${mode === "light" ? "Light" : "Dark"} colour scheme`}>
      <h3 className="set-sub">{mode === "light" ? "Light colour scheme" : "Dark colour scheme"}</h3>
      <div className="set-cards">
        {SCHEMES[mode].map((s) => (
          <button key={s.value} type="button" className="set-card" aria-label={`${mode} scheme: ${s.label}`} aria-pressed={scheme[mode] === s.value} onClick={() => save({ code_scheme: { ...scheme, [mode]: s.value } as Scheme })}>
            <span
              className={`set-sample${s.value === "none" ? " is-plain" : ""}`}
              data-surface={surface}
              data-mode={mode}
              data-amount={amount}
              data-code={s.value === "auto" || s.value === "none" ? undefined : s.value}
            >
              {SAMPLE.map((line, i) => (
                <span key={i} className="set-line">
                  {line.map(([cls, text], j) => (
                    <span key={j} className={cls ? `set-tk-${cls}` : undefined}>{text}</span>
                  ))}
                </span>
              ))}
            </span>
            <span className="set-card-name">{s.label}</span>
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <YamlFrame pageKey="board-code" file="theme.yaml" title="board & code" icon="git-compare" yaml={themeYaml(theme)}>
      <div className="set-page">
        <div className="set-title"><h1>Board &amp; code</h1><p className="lede">Each choice applies at once.</p></div>
        {error && <p className="error" role="alert">{error}</p>}

        <section className="set-section" aria-labelledby="set-code">
          <h2 id="set-code" className="set-h2">Syntax highlighting</h2>
          <p className="set-hint">{SCHEME_NOTE}</p>
          {group("light")}
          {group("dark")}
        </section>

        <section className="set-section" aria-labelledby="set-diff">
          <h2 id="set-diff" className="set-h2">Review diff</h2>
          <div className="set-diff-cols">
            <div className="set-diff-controls">
              <h3 className="set-sub">Layout</h3>
              <Segmented label="Layout" options={LAYOUTS} value={diff.layout} onChange={(v) => setDiff({ layout: v })} />
              <h3 className="set-sub">Added and removed colours</h3>
              <div className="set-cards" role="group" aria-label={ADDED_AND_REMOVED_COLOURS}>
                {DIFF_COLOURS.map((c) => (
                  <button key={c.value} type="button" className="set-card" aria-label={`Diff colours: ${c.label}`} aria-pressed={diff.colours === c.value} onClick={() => setDiff({ colours: c.value })}>
                    <span className="set-dc" data-colours={c.value}><span className="set-dc-add" /><span className="set-dc-del" /></span>
                    <span className="set-card-name">{c.label}</span>
                    <span className="set-card-note">{c.note}</span>
                  </button>
                ))}
              </div>
              <ul className="set-toggles">
                {TOGGLES.map((t) => (
                  <li key={t.key} className="set-toggle">
                    <span className="set-toggle-text"><span>{t.label}</span><span className="set-hint">{t.sub}</span></span>
                    <Switch label={t.label} checked={diff[t.key]} onChange={(v) => setDiff({ [t.key]: v })} />
                  </li>
                ))}
              </ul>
            </div>
            <div className="set-diff-preview" data-colours={diff.colours} data-layout={diff.layout} data-wrap={diff.wrap_lines} aria-label="Diff preview" role="group">
              <div className="set-diff-head">search/cache.py</div>
              <div className={unified ? "set-diff-body" : "set-diff-body is-split"} tabIndex={0} role="group" aria-label="Diff preview lines, scrolls sideways">
                <span className="set-drow is-del"><span className="set-mark">-</span><span>{diff.word_highlight ? <>ttl = <mark className="set-word">60</mark></> : "ttl = 60"}</span></span>
                <span className="set-drow is-add"><span className="set-mark">+</span><span>{diff.word_highlight ? <>ttl = <mark className="set-word">300  # seconds, a long comment that wraps or scrolls</mark></> : "ttl = 300  # seconds, a long comment that wraps or scrolls"}</span></span>
              </div>
            </div>
          </div>
        </section>

        <section className="set-section" aria-labelledby="set-board">
          <h2 id="set-board" className="set-h2">Board</h2>
          <div className="set-row">
            <span className="set-row-label">Open items in</span>
            <Segmented<BoardOpenIn> label="Open items in" options={OPEN_IN} value={theme.board.open_in} onChange={(v) => save({ board: { ...theme.board, open_in: v } })} />
            <span className="set-hint">What a row click does; {onMac() ? "⌘-click" : "Ctrl+click"} always opens the page.</span>
          </div>
        </section>

        <DefaultEditor value={theme.editor ?? null} save={(editor) => save({ editor })} />
      </div>
    </YamlFrame>
  );
}

/** The editor Open in editor uses: those this machine has, or the system's
 *  default app. A choice the machine no longer has stays listed, so it shows. */
function DefaultEditor({ value, save }: { value: string | null; save: (editor: string | null) => void }) {
  const editors = useEditors();
  const ids = typeof editors === "object" && editors ? editors.available : [];
  // Unset is not the system opener: Open in editor then takes KRAFT_EDITOR first (r12 review).
  const options = [...ids, ...(value && !ids.includes(value) ? [value] : []), ""].map((id) => ({ value: id, label: id ? editorName(id) : "Not set" }));
  return (
    <section className="set-section" aria-labelledby="set-editor">
      <h2 id="set-editor" className="set-h2">Default editor</h2>
      {typeof editors === "string" ? <p className="set-hint">{editors}</p> : (
        <Segmented label="Default editor" options={options} value={value ?? ""} onChange={(v) => save(v || null)} />
      )}
      <p className="set-hint">Not set: KRAFT_EDITOR, else the system's default app.</p>
    </section>
  );
}
