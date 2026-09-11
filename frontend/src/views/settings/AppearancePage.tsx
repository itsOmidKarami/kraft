import { useState } from "react";
import * as api from "../../api";
import { SectionLabel } from "../../components/ui";
import { PALETTES, applyTheme } from "../../theme";
import type { Theme } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5g appearance ────────────────────────────────────────────────────────── */

const MODES: { id: Theme["mode"]; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
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
    if (value) applyTheme(value.palette, value.mode);
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
              onChange={() => theme && preview({ ...theme, mode: m.id })}
            />
            {m.label}
          </label>
        ))}
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
