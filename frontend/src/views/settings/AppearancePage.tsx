import { useState } from "react";
import * as api from "../../api";
import { SectionLabel } from "../../components/ui";
import { PALETTES, applyDensity, applyTheme } from "../../theme";
import type { BoardGroupBy, Theme } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5g appearance ────────────────────────────────────────────────────────── */

const MODES: { id: Theme["mode"]; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];

const DENSITIES: { id: Theme["density"]; label: string }[] = [
  { id: "compact", label: "Compact" },
  { id: "comfortable", label: "Comfortable" },
];

const GROUP_BYS: { id: BoardGroupBy; label: string }[] = [
  { id: "status", label: "status" },
  { id: "repo", label: "repo" },
  { id: "template", label: "template" },
];

export function AppearancePage() {
  const { value, error, reload } = useResource(() => api.getTheme());
  const [draft, setDraft] = useState<Theme | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const theme = draft ?? value;
  const dirty = draft !== null;

  const preview = (next: Theme) => {
    setDraft(next);
    applyTheme(next.palette, next.mode);
    applyDensity(next.density);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTheme(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = () => {
    setDraft(null);
    if (value) {
      applyTheme(value.palette, value.mode);
      applyDensity(value.density);
    }
  };

  return (
    <>
      <PageHead
        title="Appearance"
        note="pick a palette and a light/dark mode — preview applies immediately, Save keeps it"
      />
      {error && <p className="form-error">{error}</p>}
      <SectionLabel>Palette</SectionLabel>
      <div className="palette-grid">
        {PALETTES.map((p) => (
          <button
            key={p.id}
            type="button"
            className="palette-swatch"
            aria-pressed={theme?.palette === p.id}
            disabled={!theme}
            onClick={() => theme && preview({ ...theme, palette: p.id })}
          >
            <span
              className="palette-swatch-dot"
              style={{ background: `linear-gradient(135deg, ${p.bg} 50%, ${p.accent} 50%)` }}
            />
            {p.name}
          </button>
        ))}
      </div>
      <SectionLabel>Mode</SectionLabel>
      <div className="seg" role="radiogroup" aria-label="mode">
        {MODES.map((m) => (
          <label key={m.id} className="seg-opt">
            <input
              type="radio"
              name="theme-mode"
              checked={theme?.mode === m.id}
              disabled={!theme}
              onChange={() => theme && preview({ ...theme, mode: m.id })}
            />
            {m.label}
          </label>
        ))}
      </div>

      <SectionLabel>Density</SectionLabel>
      <div className="seg" role="radiogroup" aria-label="density">
        {DENSITIES.map((d) => (
          <label key={d.id} className="seg-opt">
            <input
              type="radio"
              name="theme-density"
              checked={theme?.density === d.id}
              disabled={!theme}
              onChange={() => theme && preview({ ...theme, density: d.id })}
            />
            {d.label}
          </label>
        ))}
      </div>
      <p className="settings-foot">
        compact is the default — this interface is dense on purpose; comfortable adds 2px to row
        padding and 1px to body type
      </p>

      <SectionLabel>Board</SectionLabel>
      <div className="field">
        <label>group by</label>
        <div className="seg" role="radiogroup" aria-label="group by">
          {GROUP_BYS.map((g) => (
            <label key={g.id} className="seg-opt">
              <input
                type="radio"
                name="board-group-by"
                checked={theme?.board.group_by === g.id}
                disabled={!theme}
                onChange={() =>
                  theme && preview({ ...theme, board: { ...theme.board, group_by: g.id } })
                }
              />
              {g.label}
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <label htmlFor="board-show-done">show done</label>
        <select
          id="board-show-done"
          className="input"
          value={theme?.board.show_done ?? 5}
          disabled={!theme}
          onChange={(e) =>
            theme &&
            preview({ ...theme, board: { ...theme.board, show_done: Number(e.target.value) } })
          }
        >
          {[5, 10, 20].map((n) => (
            <option key={n} value={n}>
              last {n}
            </option>
          ))}
        </select>
        <span className="field-hint">the rest under "show all"</span>
      </div>
      <div className="field">
        <label>open items in</label>
        <div className="seg" role="radiogroup" aria-label="open items in">
          {(["peek", "full"] as const).map((o) => (
            <label key={o} className="seg-opt">
              <input
                type="radio"
                name="board-open-in"
                checked={theme?.board.open_in === o}
                disabled={!theme}
                onChange={() =>
                  theme && preview({ ...theme, board: { ...theme.board, open_in: o } })
                }
              />
              {o === "peek" ? "peek" : "full page"}
            </label>
          ))}
        </div>
        <span className="field-hint">what a row click does; ⌘-click always opens the page</span>
      </div>

      <SaveRow
        onSave={save}
        onDiscard={discard}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes theme.yaml"
      />
    </>
  );
}
