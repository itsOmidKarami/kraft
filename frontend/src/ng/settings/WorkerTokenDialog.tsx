import { useState } from "react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

/** The command on three lines, as it is typed on the machine. */
const onThreeLines = (command: string) => command.replace(/ --/g, " \\\n  --");

/** A dial-in machine's token, shown once inside the `serve` command to run on it. Kraft keeps only
 *  the hash, so closing this is the last time the token can be read. */
export function WorkerTokenDialog({ name, serveCommand, note, title, onClose }: { name: string; serveCommand: string; note: string; title?: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(serveCommand);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Dialog
      title={title ?? `${name} is added`}
      onClose={onClose}
      className="set-wk-dlg"
      footer={<Button variant="primary" onClick={onClose}>Done</Button>}
    >
      <div className="set-wk-token">
        <p className="set-wk-lead">Run this on {name}.</p>
        <div className="set-wk-cmdbox">
          <pre className="set-wk-cmd" aria-label={`Command to run on ${name}`}>{onThreeLines(serveCommand)}</pre>
          <Button className="set-wk-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
        </div>
        {note && <p className="set-hint">{note}</p>}
        <p className="set-hint set-wk-once"><Badge tone="warn">shown once</Badge> Kraft keeps only the hash of the token. If it is lost, rotate it from Edit {name}.</p>
      </div>
    </Dialog>
  );
}
