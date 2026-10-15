import "./ui.css";

export function Switch({ checked, onChange, label, disabled, "aria-describedby": describedBy }: { checked: boolean; onChange: (next: boolean) => void; label: string; disabled?: boolean; "aria-describedby"?: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} aria-describedby={describedBy} className="switch" onClick={() => onChange(!checked)}>
      <span className="switch-knob" />
    </button>
  );
}
