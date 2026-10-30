import { customRows, type CustomPart } from "./oneOff";
import "./draft/draft.css";

/** Every custom part of a one-off chain in full: what it is, where it sits, and
 *  its text (a prompt or a command as written). What a person reads before approving. */
export function OneOffParts({ custom }: { custom: CustomPart[] }) {
  return (
    <ul className="idr-custom" aria-label="Custom content">
      {customRows(custom).map((r) => (
        <li key={r.key}>
          <p className="idr-custom-what">{r.what}<span className="idr-custom-where is-mono">{r.where}</span></p>
          <pre className="idr-lines idr-custom-value">{r.text}</pre>
        </li>
      ))}
    </ul>
  );
}
