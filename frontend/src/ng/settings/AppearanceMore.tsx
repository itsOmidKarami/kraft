import type { BoardOpenIn, Theme } from "../../types";
import { DEFAULT_PREFS, type DiffPrefs } from "../review/prefs";
import { Segmented } from "../ui/Segmented";
import { editorName, useEditors } from "../item/editors";
import { Switch } from "../ui/Switch";
import { onMac } from "../keys";

type Scheme = NonNullable<Theme["code_scheme"]>;
type Mode = "light" | "dark";

const SCHEMES: { [M in Mode]: { id: Scheme[M]; name: string }[] } = {
  light: [{ id: "auto", name: "Auto" }, { id: "none", name: "None" }, { id: "solarized-light", name: "Solarized Light" }],
  dark: [{ id: "auto", name: "Auto" }, { id: "none", name: "None" }, { id: "solarized-dark", name: "Solarized Dark" }, { id: "monokai", name: "Monokai" }, { id: "dracula", name: "Dracula" }],
};

const SAMPLE: [string, string][][] = [
  [["com", "# find a work item"]],
  [["kw", "def "], ["name", "load"], ["", "(id, cache=None):"]],
  [["", "  "], ["kw", "return "], ["name", "cache"], ["", ".get(id, "], ["num", "0"], ["", ")"]],
  [["", "  "], ["str", '"missing"']],
];

const TOGGLES: { key: "show_whitespace" | "word_highlight" | "wrap_lines" | "one_file_at_a_time"; label: string; sub: string }[] = [
  { key: "show_whitespace", label: "Show whitespace changes", sub: "Off hides lines that differ only in whitespace" },
  { key: "word_highlight", label: "Highlight changed words", sub: "A stronger tint on the words that changed inside a line" },
  { key: "wrap_lines", label: "Wrap long lines", sub: "Off scrolls sideways" },
  { key: "one_file_at_a_time", label: "One file at a time", sub: "The tree stays beside a single file" },
];

const DIFF_COLOURS: { id: DiffPrefs["colours"]; name: string; note: string }[] = [
  { id: "theme", name: "Theme", note: "status colours" },
  { id: "safe", name: "Colour-blind safe", note: "blue, orange" },
  { id: "plain", name: "None", note: "marks only" },
];

/** Settings › Appearance below the colour section (UX V2 W16 D): code scheme,
 *  review diff, density, board. Each control sends its own key; `code_scheme`
 *  and `diff` go whole because `PUT /theme` merges top-level keys only. */
export function AppearanceMore({ theme, save }: { theme: Theme; save: (patch: Partial<Theme>) => void }) {
  const scheme: Scheme = theme.code_scheme ?? { light: "auto", dark: "auto" };
  const diff: DiffPrefs = theme.diff ?? DEFAULT_PREFS;
  const surface = theme.surface ?? "graphite";
  const amount = theme.colour_amount ?? "subtle";
  const setDiff = (patch: Partial<DiffPrefs>) => save({ diff: { ...diff, ...patch } });

  const group = (mode: Mode) => (
    <div className="set-code-group" role="group" aria-label={`${mode === "light" ? "Light" : "Dark"} colour scheme`}>
      <h3 className="set-sub">{mode === "light" ? "Light colour scheme" : "Dark colour scheme"}</h3>
      <div className="set-cards">
        {SCHEMES[mode].map((s) => (
          <button key={s.id} type="button" className="set-card" aria-label={`${mode} scheme: ${s.name}`} aria-pressed={scheme[mode] === s.id} onClick={() => save({ code_scheme: { ...scheme, [mode]: s.id } as Scheme })}>
            <span
              className={`set-sample${s.id === "none" ? " is-plain" : ""}`}
              data-surface={surface}
              data-mode={mode}
              data-amount={amount}
              data-code={s.id === "auto" || s.id === "none" ? undefined : s.id}
            >
              {SAMPLE.map((line, i) => (
                <span key={i} className="set-line">
                  {line.map(([cls, text], j) => (
                    <span key={j} className={cls ? `set-tk-${cls}` : undefined}>{text}</span>
                  ))}
                </span>
              ))}
            </span>
            <span className="set-card-name">{s.name}</span>
          </button>
        ))}
      </div>
    </div>
  );

  const unified = diff.layout === "unified";
  return (
    <div className="set-more">
      <section aria-labelledby="set-code">
        <h2 id="set-code">Syntax highlighting</h2>
        <p className="set-hint">Colours code in review diffs. Chosen separately for light and dark. Auto follows Colour amount; None is a single colour.</p>
        {group("light")}
        {group("dark")}
      </section>

      <section aria-labelledby="set-diff">
        <h2 id="set-diff">Review diff</h2>
        <div className="set-diff-cols">
          <div className="set-diff-controls">
            <h3 className="set-sub">Layout</h3>
            <Segmented label="Layout" options={[{ value: "split", label: "Side-by-side" }, { value: "unified", label: "Inline" }]} value={diff.layout} onChange={(v) => setDiff({ layout: v })} />
            <h3 className="set-sub">Added and removed colours</h3>
            <div className="set-cards" role="group" aria-label="Added and removed colours">
              {DIFF_COLOURS.map((c) => (
                <button key={c.id} type="button" className="set-card" aria-label={`Diff colours: ${c.name}`} aria-pressed={diff.colours === c.id} onClick={() => setDiff({ colours: c.id })}>
                  <span className="set-dc" data-colours={c.id}><span className="set-dc-add" /><span className="set-dc-del" /></span>
                  <span className="set-card-name">{c.name}</span>
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

      <section aria-labelledby="set-density">
        <h2 id="set-density">Density</h2>
        <Segmented label="Density" options={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }]} value={theme.density} onChange={(v) => save({ density: v })} />
        <p className="set-hint">Compact is the default. This interface is dense on purpose. Comfortable adds 2px to row padding and 1px to body type.</p>
      </section>

      <section aria-labelledby="set-board">
        <h2 id="set-board">Board</h2>
        <div className="set-row">
          <span className="set-row-label">Open items in</span>
          <Segmented<BoardOpenIn> label="Open items in" options={[{ value: "peek", label: "Peek" }, { value: "full", label: "Full page" }]} value={theme.board.open_in} onChange={(v) => save({ board: { ...theme.board, open_in: v } })} />
          <span className="set-hint">What a row click does; {onMac() ? "⌘-click" : "Ctrl+click"} always opens the page.</span>
        </div>
      </section>

      <DefaultEditor value={theme.editor ?? null} save={(editor) => save({ editor })} />
    </div>
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
    <section aria-labelledby="set-editor">
      <h2 id="set-editor">Default editor</h2>
      {typeof editors === "string" ? <p className="set-hint">{editors}</p> : (
        <Segmented label="Default editor" options={options} value={value ?? ""} onChange={(v) => save(v || null)} />
      )}
      <p className="set-hint">Not set: KRAFT_EDITOR, else the system's default app.</p>
    </section>
  );
}
