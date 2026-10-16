import { ValueCell } from "../templates/draft/ValueCell";

/** A harness field edited in place, as every pane's values are (ValueCell): a click opens it, Enter or
 *  leaving it commits, Escape cancels. `changed` marks it until published. An empty text is refused
 *  unless `clearable`: then it clears the value. `suggestions` are the known values, any other may still be typed. */
export function ModelField({ label, value, suggestions = [], changed, clearable, placeholder, onCommit }: {
  label: string;
  clearable?: boolean;
  placeholder?: string;
  value: string;
  suggestions?: string[];
  changed?: boolean;
  onCommit: (v: string) => void;
}) {
  return (
    <div className="hn-field">
      <span className="hn-field-label">{label}</span>
      <ValueCell
        label={label}
        value={value}
        display={value || placeholder || "not set"}
        muted={!value}
        changed={changed}
        placeholder={placeholder}
        choices={suggestions.length ? suggestions.map((v) => ({ value: v })) : undefined}
        onCommit={(text) => {
          if (!text && !clearable) return `${label} can't be empty.`;
          onCommit(text);
          return null;
        }}
      />
    </div>
  );
}
