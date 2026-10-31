import type { ForgeTokenSource } from "../../types";
import { detailOf, request } from "../http";
import { Field } from "../ui/Field";
import { Segmented } from "../ui/Segmented";
import { Select } from "../ui/Select";
import { showToast } from "../ui/Toast";
import { COMMAND_READ_ONLY, ENV_RESTART, PASTE_ONCE } from "./forgeWords";

export type SourceChoice = "git-credential" | "env" | "file" | "paste";
export type Kind = ForgeTokenSource["kind"];

const SOURCES: { value: SourceChoice; label: string }[] = [
  { value: "git-credential", label: "git credential" },
  { value: "env", label: "variable" },
  { value: "file", label: "file" },
  { value: "paste", label: "paste" },
];
// Never `command`: it is code run on the server's machine, set only there. The server's PUT /token and a PATCH that
// sends `client: "cli"` or `token: null` do not stop a browser from replacing or erasing one, so leaving it out of
// the UI (here, and the paste and client controls on a command account) is the only guard.

/** The token as the API takes it; null for a paste, which goes in its own PUT. */
export function sourceBody(choice: SourceChoice, env: string, file: string, kind: Kind): ForgeTokenSource | null {
  if (choice === "paste") return null;
  if (choice === "env") return { from: "env", env: env.trim(), kind };
  if (choice === "file") return { from: "file", file: file.trim(), kind };
  return { from: "git-credential", kind };
}

export const KindSelect = ({ kind, onKind, label = "Token kind" }: { kind: Kind; onKind: (k: Kind) => void; label?: string }) => (
  <Field label={label}>
    <Select value={kind} onChange={(e) => onKind(e.target.value as Kind)}>
      <option value="pat">pat</option>
      <option value="oauth">oauth</option>
      <option value="unknown">unknown</option>
    </Select>
  </Field>
);

/** The pasted token. A password input, but `autoComplete="off"` is ignored on those: `new-password` and the two
 *  data attributes keep browsers and password managers from offering to save a forge token as a login. */
export const PasteInput = ({ value, onChange, label = "Token" }: { value: string; onChange: (v: string) => void; label?: string }) => (
  <Field label={label} hint={PASTE_ONCE}>
    <input type="password" autoComplete="new-password" data-1p-ignore data-lpignore="true" spellCheck={false} value={value} onChange={(e) => onChange(e.target.value)} />
  </Field>
);

export function TokenSourceField({ allowPaste, choice, onChoice, env, onEnv, file, onFile, kind, onKind, paste, onPaste }: {
  allowPaste: boolean; choice: SourceChoice; onChoice: (c: SourceChoice) => void; env: string; onEnv: (v: string) => void; file: string; onFile: (v: string) => void;
  kind: Kind; onKind: (k: Kind) => void; paste: string; onPaste: (v: string) => void;
}) {
  return (
    <>
      <div className="set-wk-field">
        <span className="field-label">Token from</span>
        <Segmented label="Token from" value={choice} onChange={(c) => { if (c !== "paste") onPaste(""); onChoice(c); }} options={allowPaste ? SOURCES : SOURCES.filter((s) => s.value !== "paste")} />
      </div>
      {choice === "env" && <Field label="Variable name" hint={ENV_RESTART}><input value={env} onChange={(e) => onEnv(e.target.value)} /></Field>}
      {choice === "file" && <Field label="File path"><input value={file} onChange={(e) => onFile(e.target.value)} /></Field>}
      {choice === "paste" && <PasteInput value={paste} onChange={onPaste} />}
      <KindSelect kind={kind} onKind={onKind} />
    </>
  );
}

export const CommandSource = ({ name, command }: { name: string; command: string }) => (
  <div className="set-wk-field">
    <span className="field-label">Token command</span>
    <pre className="set-wk-cmd" aria-label="Token command">{command}</pre>
    <span className="field-hint">{COMMAND_READ_ONLY(name)}</span>
  </div>
);

/** PUT a pasted token: the one request that carries it. The caller has already cleared its field. */
export const putToken = (name: string, value: string, kind: Kind) =>
  request(`/forges/${encodeURIComponent(name)}/token`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ value, kind }) });

/** POST /test after a token changed (the probe the server kicks runs in the background, so a re-read shows the old
 *  state), then a toast with its first line. Up to 90 s. */
export async function checkAndToast(name: string, lead: string) {
  const r = await request<{ ok: boolean; output: string }>(`/forges/${encodeURIComponent(name)}/test`, { method: "POST" });
  if (r.status !== 200) return showToast(`${lead} ${detailOf(r.body)}`, { tone: "error" });
  showToast(`${lead} ${r.body.output.split("\n")[0]}`, r.body.ok ? undefined : { tone: "error" });
}
