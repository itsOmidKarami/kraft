import { addedCheckText, customRows, type AddedCheck, type CustomPart } from "./oneOff";
import "./draft/draft.css";

/** Every custom part of a one-off chain in full: what it is and its text (a
 *  prompt or a command as written). What a person reads before approving. */
export function OneOffParts({ custom }: { custom: CustomPart[] }) {
  return (
    <ul className="idr-custom" aria-label="Custom content">
      {customRows(custom).map((r) => (
        <li key={r.key}>
          <p className="idr-custom-what">{r.what}</p>
          {/* A long part scrolls in its box: focusable, so a keyboard scrolls it too. */}
          <pre className="idr-lines idr-custom-value" tabIndex={0} role="region" aria-label={r.what}>{r.text}</pre>
        </li>
      ))}
    </ul>
  );
}

const List = ({ label, ids }: { label: string; ids: string[] }) => (ids.length ? <p className="idr-sub"><b>{label}</b> <span className="is-mono">{ids.join(", ")}</span></p> : null);

/** What a one-off chain did to the chain it extends, the custom parts under `custom content`, and the checks on each node it adds. */
export function OneOffSummary({ changes, custom, added }: { changes: { added: string[]; overridden: string[]; removed: string[] }; custom: CustomPart[]; added: AddedCheck[] }) {
  return (
    <>
      <List label="Added" ids={changes.added} />
      <List label="Overridden" ids={changes.overridden} />
      <List label="Removed" ids={changes.removed} />
      {custom.length > 0 && (
        <>
          <p className="idr-h">custom content</p>
          <OneOffParts custom={custom} />
        </>
      )}
      {added.length > 0 && <ul className="idr-checks">{added.map((a) => <li key={a.node} className="is-ok">✓ {addedCheckText(a)}</li>)}</ul>}
    </>
  );
}
